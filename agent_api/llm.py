"""大模型客户端：urllib 直调任意 OpenAI 兼容接口，零第三方依赖。

LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 三个变量填上即可；
没 Key 时自动降级为 MockLLM，方便先跑通链路。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings
from .errors import LLMError
from .utils import new_id

logger = logging.getLogger("agent.llm")
_RETRY_STATUS = {429, 500, 502, 503, 504}


class StreamAborted(Exception):
    """上游流式连接中途断开（Errno 103 / RST 之类），且还没吐出任何内容。"""


def rate_limit_per_minute() -> int:
    """读取 RATE_LIMIT_PER_MIN；0 = 不限速。默认 7，给额度很小的测试 Key 用。"""
    raw = (os.getenv("RATE_LIMIT_PER_MIN") or "7").strip()
    try:
        value = int(float(raw))
    except ValueError:
        return 7
    return max(0, value)


class _Gate:
    """全局限速：主 Agent 和所有子 Agent 共用同一个配额，保证不超每分钟上限。"""

    def __init__(self, per_minute: int):
        self.per_minute = max(0, per_minute)
        self.interval = 60.0 / self.per_minute if self.per_minute else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next_at - now
            self._next_at = max(now, self._next_at) + self.interval
        if delay > 0:
            logger.info("限速：等待 %.1fs（上限 %d 次/分钟）", delay, self.per_minute)
            time.sleep(delay)


class BaseLLM:
    def __init__(self, settings: Settings):
        self.settings = settings

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_event: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """默认实现：不支持流式就退回一次性，把整段当一次 delta 发出去。"""
        message = self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        if on_event and message.get("reasoning"):
            on_event({"type": "reasoning_delta", "text": message["reasoning"]})
        if on_event and message.get("content"):
            on_event({"type": "content_delta", "text": message["content"]})
        return message


def _delta_text(delta: dict[str, Any]) -> tuple[str, str]:
    """从 delta 里挖出（思考, 正文）。中转站字段五花八门，这里都认。"""
    think = ""
    for key in ("reasoning_content", "reasoning", "thinking", "reasoning_text"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            think = value
            break
        if isinstance(value, dict):
            nested = value.get("content") or value.get("text")
            if isinstance(nested, str) and nested:
                think = nested
                break
    content = delta.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return think, content if isinstance(content, str) else ""


class OpenAICompatibleLLM(BaseLLM):
    def __init__(self, settings: Settings, per_minute: int | None = None):
        super().__init__(settings)
        self.gate = _Gate(rate_limit_per_minute() if per_minute is None else per_minute)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": self.settings.llm_max_tokens if max_tokens is None else max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.settings.llm_base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error = ""

        for attempt in range(3):
            self.gate.wait()
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.settings.llm_api_key}",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.settings.llm_timeout) as resp:
                    body = resp.read().decode("utf-8", "replace")
                return self._normalize(json.loads(body))
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:800]
                except Exception:  # noqa: BLE001
                    pass
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code == 429:
                    wait = 0.0
                    header = exc.headers.get("Retry-After") if exc.headers else None
                    if header:
                        try:
                            wait = float(header)
                        except ValueError:
                            wait = 0.0
                    time.sleep(min(max(wait, 1.0), 60.0))
                if exc.code not in _RETRY_STATUS:
                    break
            except (socket.timeout, TimeoutError) as exc:
                last_error = f"请求超时：{exc}"
            except urllib.error.URLError as exc:
                last_error = f"网络错误：{exc.reason}"
            except (ConnectionError, OSError) as exc:
                last_error = f"连接被中断：{exc}"
            except json.JSONDecodeError as exc:
                last_error = f"模型返回不是合法 JSON：{exc}"
                break
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

        raise LLMError(
            f"调用模型失败（{self.settings.llm_base_url} / {self.settings.llm_model}）：{last_error}"
        )

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_event: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": self.settings.llm_max_tokens if max_tokens is None else max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.settings.llm_base_url}/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error = ""

        for attempt in range(3):
            self.gate.wait()
            request = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Authorization": f"Bearer {self.settings.llm_api_key}",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.settings.llm_timeout) as resp:
                    return self._read_stream(resp, on_event)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:600]
                except Exception:  # noqa: BLE001
                    pass
                last_error = f"HTTP {exc.code}：{detail}"
                if exc.code not in _RETRY_STATUS:
                    break
                if exc.code == 429:
                    header = exc.headers.get("Retry-After") if exc.headers else None
                    wait = 1.0
                    if header:
                        try:
                            wait = float(header)
                        except ValueError:
                            wait = 1.0
                    time.sleep(min(max(wait, 1.0), 60.0))
            except (socket.timeout, TimeoutError) as exc:
                last_error = f"流式请求超时：{exc}"
            except urllib.error.URLError as exc:
                last_error = f"网络错误：{exc.reason}"
            except StreamAborted as exc:
                last_error = f"连接被中断：{exc}"
                logger.info("模型流被中断，重试第 %s 次", attempt + 1)
            except (ConnectionError, OSError) as exc:
                last_error = f"连接被中断：{exc}"
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

        raise LLMError(
            f"调用模型失败（{self.settings.llm_base_url} / {self.settings.llm_model}）：{last_error}"
        )

    def _read_stream(self, resp: Any, on_event: Any) -> dict[str, Any]:
        content_parts: list[str] = []
        reason_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        aborted = {"hit": False}

        def safe_lines(stream: Any) -> Any:
            try:
                for item in stream:
                    yield item
            except (ConnectionError, OSError, ValueError):
                aborted["hit"] = True
                return

        for raw_line in safe_lines(resp):
            line = raw_line.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith("data:"):
                data = line[5:].strip()
                if data in {"[DONE]", "DONE"}:
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
            elif line.startswith("{"):
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
            else:
                continue
            if not isinstance(chunk, dict):
                continue
            error = chunk.get("error")
            if isinstance(error, dict) and error:
                raise LLMError(f"模型返回错误：{error.get('message') or json.dumps(error, ensure_ascii=False)[:300]}")
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or choices[0].get("message") or {}
            think, text = _delta_text(delta)
            if think:
                reason_parts.append(think)
                if on_event:
                    try:
                        on_event({"type": "reasoning_delta", "text": think})
                    except Exception:  # noqa: BLE001
                        pass
            if text:
                content_parts.append(text)
                if on_event:
                    try:
                        on_event({"type": "content_delta", "text": text})
                    except Exception:  # noqa: BLE001
                        pass
            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                idx = tc.get("index")
                if not isinstance(idx, int):
                    idx = len(calls)
                rec = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if isinstance(tc.get("id"), str) and tc["id"]:
                    rec["id"] = tc["id"]
                fn = tc.get("function") or {}
                name = fn.get("name")
                if isinstance(name, str) and name:
                    rec["name"] = name if not rec["name"] else rec["name"]
                args = fn.get("arguments")
                if isinstance(args, str):
                    rec["arguments"] += args
                elif isinstance(args, dict):
                    rec["arguments"] += json.dumps(args, ensure_ascii=False)

        if aborted["hit"]:
            if not content_parts and not reason_parts and not calls:
                raise StreamAborted("模型流还没吐字就断了")
            logger.warning("模型流传输中断，保留已收到的内容（%s 字）", sum(len(x) for x in content_parts))

        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if aborted["hit"]:
            message["truncated"] = True
            if message["content"].strip():
                message["content"] += "\n\n……（网络中断，这条回复被截断了：直接再发一次「继续」即可）"
        reasoning = "".join(reason_parts).strip()
        if reasoning:
            message["reasoning"] = reasoning
        normalized = []
        for idx in sorted(calls):
            if aborted["hit"]:
                break
            rec = calls[idx]
            if not rec.get("name"):
                continue
            normalized.append(
                {
                    "id": rec.get("id") or new_id("call"),
                    "type": "function",
                    "function": {"name": rec["name"], "arguments": rec.get("arguments") or "{}"},
                }
            )
        if normalized:
            message["tool_calls"] = normalized
        return message

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"模型返回为空：{json.dumps(data, ensure_ascii=False)[:500]}")
        message = dict(choices[0].get("message") or {})
        message.setdefault("role", "assistant")
        message["content"] = message.get("content") or ""
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            message["reasoning"] = reasoning.strip()
        message.pop("reasoning_content", None)
        calls = message.get("tool_calls") or []
        normalized = []
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            args = fn.get("arguments")
            if isinstance(args, (dict, list)):
                args = json.dumps(args, ensure_ascii=False)
            normalized.append(
                {
                    "id": call.get("id") or new_id("call"),
                    "type": "function",
                    "function": {"name": name, "arguments": args or "{}"},
                }
            )
        if normalized:
            message["tool_calls"] = normalized
        else:
            message.pop("tool_calls", None)
        return message


class MockLLM(BaseLLM):
    """没有 API Key 时的离线替身：真实走一次 run_python，用来验证工具闭环。"""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        tool_names = {t.get("function", {}).get("name") for t in (tools or [])}
        if not has_tool_result and "run_python" in tool_names:
            return {
                "role": "assistant",
                "content": "（Mock 模式）先用工具跑一段 Python 验证环境。",
                "tool_calls": [
                    {
                        "id": new_id("call"),
                        "type": "function",
                        "function": {
                            "name": "run_python",
                            "arguments": json.dumps({"code": "print('hello from mock llm')"}, ensure_ascii=False),
                        },
                    }
                ],
            }
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = str(msg.get("content") or "")
                break
        return {"role": "assistant", "content": f"（Mock 模式，未配置 LLM_API_KEY）已收到任务：{last_user[:200]}"}

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_event: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        message = self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        reason = "（Mock 模式）先看环境，再决定用不用工具。"
        if on_event:
            on_event({"type": "reasoning_delta", "text": reason})
        content = str(message.get("content") or "")
        step = max(1, len(content) // 40) if content else 1
        for i in range(0, len(content), step):
            if on_event:
                on_event({"type": "content_delta", "text": content[i:i + step]})
                time.sleep(0.01)
        message["reasoning"] = reason
        return message


def build_llm(settings: Settings) -> BaseLLM:
    if settings.use_mock_llm:
        logger.warning("未配置 LLM_API_KEY，当前使用 MockLLM（仅用于联调）")
        return MockLLM(settings)
    return OpenAICompatibleLLM(settings, rate_limit_per_minute())
