"""大模型客户端：urllib 直调任意 OpenAI 兼容接口，零第三方依赖。

LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 三个变量填上即可；
没 Key 时自动降级为 MockLLM，方便先跑通链路。
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings
from .errors import LLMError
from .utils import new_id

logger = logging.getLogger("agent.llm")
_RETRY_STATUS = {429, 500, 502, 503, 504}


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


class OpenAICompatibleLLM(BaseLLM):
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
                if exc.code not in _RETRY_STATUS:
                    break
            except (socket.timeout, TimeoutError) as exc:
                last_error = f"请求超时：{exc}"
            except urllib.error.URLError as exc:
                last_error = f"网络错误：{exc.reason}"
            except json.JSONDecodeError as exc:
                last_error = f"模型返回不是合法 JSON：{exc}"
                break
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

        raise LLMError(
            f"调用模型失败（{self.settings.llm_base_url} / {self.settings.llm_model}）：{last_error}"
        )

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"模型返回为空：{json.dumps(data, ensure_ascii=False)[:500]}")
        message = dict(choices[0].get("message") or {})
        message.setdefault("role", "assistant")
        message["content"] = message.get("content") or ""
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


def build_llm(settings: Settings) -> BaseLLM:
    if settings.use_mock_llm:
        logger.warning("未配置 LLM_API_KEY，当前使用 MockLLM（仅用于联调）")
        return MockLLM(settings)
    return OpenAICompatibleLLM(settings)
