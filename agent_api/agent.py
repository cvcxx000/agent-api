"""Agent 主循环：模型 → 工具 → 观察 → 再模型，直到给出最终答复（同步实现）。

模型这一步支持流式：边想边发 reasoning_delta / content_delta，网页和终端都能
实时看到「思考过程」和正文，而不是等整段回来才刷一次。
"""

from __future__ import annotations

import base64
import json
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import providers as provider_store
from .security import active_root
from .errors import AgentCancelled, LLMError
from .sessions import Session
from .tools.base import ToolContext

logger = logging.getLogger("agent.core")

Emit = Callable[[dict[str, Any]], None]

# 工具结果里的看图标记：view_image 返回 "VIEW_IMAGE: /abs/path"
_VIEW_RE = re.compile(r"^VIEW_IMAGE:\s*(\S+)\s*$", re.M)

_MAX_INLINE_IMAGE = 4 * 1024 * 1024
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")


def estimate_tokens(text: str) -> int:
    """零依赖粗估：CJK 约 1 字 1 token，其余按 3.5 字符 1 token。"""
    text = str(text or "")
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return int(cjk + other / 3.5) + 1


def _prefix_for_tokens(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def _suffix_for_tokens(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[-mid:]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[-low:] if low else ""


@dataclass
class AgentResult:
    content: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0


def _trim_for_model(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """历史过长时从最近的 user 消息开始截断，保证 tool_call / tool 配对不被切开。"""
    if limit <= 0 or len(messages) <= limit:
        return messages
    start = len(messages) - limit
    while start < len(messages) and messages[start].get("role") != "user":
        start += 1
    return messages[start:] if start < len(messages) else messages[-limit:]


def _message_chars(message: dict[str, Any]) -> int:
    """粗算一条消息占多少字符；图片附件不算，因为 base64 不随历史重复发。"""
    content = message.get("content")
    size = len(content) if isinstance(content, str) else len(json.dumps(content, ensure_ascii=False))
    calls = message.get("tool_calls")
    if calls:
        size += len(json.dumps(calls, ensure_ascii=False))
    return size


def _message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    calls = message.get("tool_calls")
    if calls:
        text += json.dumps(calls, ensure_ascii=False)
    return estimate_tokens(text)


def _trim_for_context(
    messages: list[dict[str, Any]], chars: int, tokens: int = 0
) -> list[dict[str, Any]]:
    """按字符 / token 预算保留最近的历史，并从 user 消息处切开，避免 tool 结果变成孤儿。"""
    if (chars <= 0 and tokens <= 0) or not messages:
        return messages
    total_chars = 0
    total_tokens = 0
    start = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        size = _message_chars(messages[index])
        size_tokens = _message_tokens(messages[index])
        over_chars = chars > 0 and total_chars + size > chars
        over_tokens = tokens > 0 and total_tokens + size_tokens > tokens
        if (over_chars or over_tokens) and start < len(messages):
            break
        total_chars += size
        total_tokens += size_tokens
        start = index
    while start < len(messages) and messages[start].get("role") != "user":
        start += 1
    if start >= len(messages):
        # 当前这一轮本身就超预算：退回最近的 user，保证 assistant/tool 配对完整，
        # 宁可这一轮略超，也不要发一条没有 tool_call 的孤儿 tool 结果给模型。
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return messages[index:]
        return messages[-1:]
    return messages[start:]


def _fit_system(text: str, limit_chars: int, limit_tokens: int = 0) -> str:
    """系统提示词硬上限：保留头尾，避免整份长 Skill 每一轮都重发。"""
    text = text or ""
    original_len = len(text)
    if limit_chars > 0:
        text = text[:limit_chars]
    if limit_tokens <= 0 or estimate_tokens(text) <= limit_tokens:
        return text
    note_len = 220
    body = max(0, limit_tokens - note_len)
    head = _prefix_for_tokens(text, int(body * 0.62))
    tail = _suffix_for_tokens(text, body - estimate_tokens(head))
    return (
        head
        + f"\n\n...[系统提示词过长：共 {original_len} 字符，只发头 {len(head)} + 尾 {len(tail)} 字符；"
        f"完整内容用 get_system_prompt(offset=...) 分段看]...\n\n"
        + tail
    )


_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


class Agent:
    def __init__(
        self,
        runtime: Any,
        session: Session,
        ctx: ToolContext,
        max_turns: int | None = None,
        cancel_event: Any = None,
    ):
        self.runtime = runtime
        self.settings = runtime.settings
        self.session = session
        self.ctx = ctx
        self.messages = session.messages
        self.max_turns = max_turns or session.max_turns or self.settings.max_turns
        self.cancel_event = cancel_event
        self._emit_sink: Emit | None = None
        self._warned_vision = False
        self._warned_context = False
        try:
            self.vision = bool(provider_store.active_meta(self.settings).get("vision"))
        except Exception:  # noqa: BLE001 - 读不到就按不支持图片处理
            self.vision = False

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise AgentCancelled("已取消")

    def _emit(self, emit: Emit | None, event: dict[str, Any]) -> None:
        if emit is None:
            return
        try:
            emit(event)
        except Exception:  # noqa: BLE001 - 事件通道异常不能影响主流程
            logger.debug("事件发送失败", exc_info=True)

    def _parts_for(self, message: dict[str, Any]) -> Any:
        """用户消息带附件时：模型能吃图就发多模态内容块，不能就明确告知（不再静默丢弃）。"""
        raw_content = message.get("content")
        if isinstance(raw_content, list):
            return raw_content
        attachments = message.get("attachments") or []
        if attachments and not self.vision:
            doc_pages = [
                a for a in attachments if isinstance(a, dict) and str(a.get("type") or "") == "docpage"
            ]
            user_images = [
                a for a in attachments if not (isinstance(a, dict) and str(a.get("type") or "") == "docpage")
            ]
            if user_images and not self._warned_vision:
                self._warned_vision = True
                self._emit(
                    self._emit_sink,
                    {
                        "type": "warning",
                        "message": "图片附件没有发出去：当前模型被判定为不支持看图（vision 关）。"
                        "在「提供商 → 视觉」里打开，或换 gpt-4o / *-vl / gemini 这类模型。",
                    },
                )
            if doc_pages and not user_images:
                tail = "\n\n[文档页面图没能挂上：当前模型不支持图片输入，这次只发了文字]"
            else:
                tail = "\n\n[附件没能发给模型：当前模型不支持图片输入，只能当文件路径给我]"
            return str(message.get("content") or "") + tail
        if self.vision and attachments:
            parts: list[dict[str, Any]] = [{"type": "text", "text": str(message.get("content") or "")}]
            for item in attachments[:8]:
                rel = str(item.get("path") if isinstance(item, dict) else item or "")
                if not rel:
                    continue
                suffix = Path(rel).suffix.lower()
                if suffix not in _MIME:
                    continue
                target = active_root() / rel
                try:
                    raw = target.read_bytes()
                except OSError:
                    continue
                if len(raw) > _MAX_INLINE_IMAGE:
                    continue
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{_MIME[suffix]};base64," + base64.b64encode(raw).decode("ascii")},
                    }
                )
            if len(parts) > 1:
                return parts
        return str(message.get("content") or "")

    def _payload(self, tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        system = self.session.system_prompt
        extra = dict(getattr(self.ctx, "extra", None) or {})
        if extra.get("guest"):
            unlocked = bool(extra.get("guest_unlocked"))
            hint = "已解锁，但同样只能动这个目录。" if unlocked else "尚未解锁：需要游客在账号面板输入解锁码；不要尝试绕过。"
            system += (
                "\n\n[游客模式] 你现在服务的是一个临时游客：只在 ta 自己的独立目录里活动（路径相对当前用户目录），"
                "不能派子 Agent、不能用插件 / 定时任务 / Cookie，也不能改任何全局设置。"
                "Python 与终端" + hint + " 每分钟只能发一条消息、空间 100MB，回答尽量短。"
            )
        if extra.get("api_client"):
            system += (
                "\n\n[API 客户端模式] 请求来自第三方 OpenAI 兼容客户端：工作目录不设 GB 限额，"
                "但不提供联网搜索工具，搜索由客户端自己负责；不要假装自己搜过网页。"
            )

        tools_tokens = estimate_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0
        available = 0
        if self.settings.context_tokens > 0:
            available = self.settings.context_tokens - self.settings.llm_max_tokens - tools_tokens
            available = max(1500, available)
        system_budget = self.settings.system_max_tokens
        if available:
            system_budget = min(system_budget, max(800, int(available * 0.4)))
        system = _fit_system(system, self.settings.system_max_chars, system_budget)
        history_tokens = max(1200, available - estimate_tokens(system)) if available else 0
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        history = _trim_for_model(self.messages, self.settings.history_limit)
        history = _trim_for_context(history, self.settings.history_chars, history_tokens)
        for message in history:
            clean = {k: v for k, v in message.items() if k not in {"reasoning", "attachments"}}
            if clean.get("role") == "user":
                clean["content"] = self._parts_for(message)
            elif isinstance(clean.get("content"), list):
                clean["content"] = json.dumps(clean["content"], ensure_ascii=False)
            out.append(clean)
        return out

    def _model_step(self, emit: Emit | None, tools: list[dict[str, Any]], turn: int) -> dict[str, Any]:
        payload = self._payload(tools)
        if emit is None or not hasattr(self.runtime.llm, "stream_chat"):
            return self.runtime.llm.chat(payload, tools=tools)

        def forward(event: dict[str, Any]) -> None:
            kind = event.get("type")
            if kind == "reasoning_delta":
                self._emit(emit, {"type": "reasoning_delta", "turn": turn, "text": event.get("text", "")})
            elif kind == "content_delta":
                self._emit(emit, {"type": "content_delta", "turn": turn, "text": event.get("text", "")})

        return self.runtime.llm.stream_chat(payload, tools=tools, on_event=forward)

    def _record_usage(self, message: dict[str, Any], emit: Emit | None, turn: int) -> None:
        usage = message.get("usage")
        if not isinstance(usage, dict) or not usage:
            return
        box = self.session.usage
        prompt = int(usage.get("prompt") or 0)
        completion = int(usage.get("completion") or 0)
        cached = int(usage.get("cached") or 0)
        box["prompt"] = int(box.get("prompt") or 0) + prompt
        box["completion"] = int(box.get("completion") or 0) + completion
        box["cached"] = int(box.get("cached") or 0) + max(0, min(cached, prompt))
        box["total"] = int(box.get("total") or 0) + int(usage.get("total") or (prompt + completion))
        box["calls"] = int(box.get("calls") or 0) + 1
        box["last_prompt"] = prompt
        box["last_completion"] = completion
        box["last_cached"] = max(0, min(cached, prompt))
        limit = int(self.settings.context_tokens or 0)
        if limit and prompt >= int(limit * 0.9) and not self._warned_context:
            self._warned_context = True
            self._emit(
                emit,
                {
                    "type": "warning",
                    "message": f"本轮提示 {prompt} tokens，已到上下文上限 {limit} 的 90%。"
                    "系统提示词或历史太长：建议新开会话，或把长 Skill 改成按需 read_skill。",
                },
            )
        self._emit(emit, {"type": "usage", "turn": turn, "usage": dict(box)})

    def run(self, user_text: str, emit: Emit | None = None, attachments: list[dict[str, Any]] | None = None) -> AgentResult:
        self._emit_sink = emit
        steps: list[dict[str, Any]] = []
        user_message: dict[str, Any] = {"role": "user", "content": user_text}
        if attachments:
            user_message["attachments"] = attachments
        self.messages.append(user_message)
        self._emit(emit, {"type": "user", "content": user_text, "attachments": attachments or []})

        final_text = ""
        turns = 0
        for turn in range(self.max_turns):
            self._check_cancel()
            turns = turn + 1
            tools = self.runtime.registry.openai_specs(self.ctx)
            try:
                message = self._model_step(emit, tools, turns)
            except LLMError as exc:
                self._emit(emit, {"type": "error", "message": exc.message})
                raise
            self._record_usage(message, emit, turns)

            content = message.get("content") or ""
            reasoning = str(message.get("reasoning") or "")
            tool_calls = message.get("tool_calls") or []

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if reasoning:
                assistant_msg["reasoning"] = reasoning
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.messages.append(assistant_msg)

            if content or reasoning:
                steps.append({"type": "assistant", "turn": turns, "content": content, "reasoning": reasoning})
                self._emit(emit, {"type": "assistant", "turn": turns, "content": content, "reasoning": reasoning})

            if not tool_calls:
                final_text = content
                break

            fresh_images: list[str] = []
            for call in tool_calls:
                self._check_cancel()
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                arguments: dict[str, Any] = {}
                try:
                    parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    arguments = parsed if isinstance(parsed, dict) else {"value": parsed}
                except json.JSONDecodeError:
                    result = f"ERROR: 工具参数不是合法 JSON：{str(raw_args)[:300]}"
                else:
                    self._emit(
                        emit,
                        {"type": "tool_call", "turn": turns, "id": call.get("id"), "name": name, "arguments": arguments},
                    )
                    result = self.runtime.registry.execute(name, arguments, self.ctx)

                for one in _VIEW_RE.findall(result or ""):
                    if one not in fresh_images and len(fresh_images) < 4:
                        fresh_images.append(one)

                self.messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name, "content": result})
                steps.append(
                    {"type": "tool", "turn": turns, "id": call.get("id"), "name": name, "arguments": arguments, "output": result}
                )
                self._emit(
                    emit, {"type": "tool_result", "turn": turns, "id": call.get("id"), "name": name, "output": result}
                )
            if fresh_images:
                if self.vision:
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "【view_image 取到的图片】直接看图回答上面的问题，"
                                "不要再用 run_python 读像素或猜内容。"
                            ),
                            "attachments": [{"path": p, "type": "image", "name": Path(p).name} for p in fresh_images],
                        }
                    )
                else:
                    self.messages.append(
                        {
                            "role": "user",
                            "content": "[view_image 取到的图片没能发给模型：当前模型被判定为不支持看图]",
                        }
                    )
                    if not self._warned_vision:
                        self._warned_vision = True
                        self._emit(emit, {"type": "warning", "message": "图片没发出去：当前模型 vision 是关的。"})
        else:
            final_text = (
                f"（已达到单轮最大工具调用轮次 {self.max_turns}，任务可能未完成。"
                "可以让我继续，或把任务拆成更小的子任务。）"
            )
            self._emit(emit, {"type": "warning", "message": final_text})

        self.session.messages = self.messages
        self.runtime.sessions.save(self.session)
        self._emit(emit, {"type": "end", "content": final_text, "turns": turns})
        return AgentResult(content=final_text, messages=self.messages, steps=steps, turns=turns)
