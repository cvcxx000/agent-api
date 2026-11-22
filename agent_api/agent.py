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
from .errors import AgentCancelled, LLMError
from .sessions import Session
from .tools.base import ToolContext

logger = logging.getLogger("agent.core")

Emit = Callable[[dict[str, Any]], None]

# 工具结果里的看图标记：view_image 返回 "VIEW_IMAGE: /abs/path"
_VIEW_RE = re.compile(r"^VIEW_IMAGE:\s*(\S+)\s*$", re.M)

_MAX_INLINE_IMAGE = 4 * 1024 * 1024


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
            if not self._warned_vision:
                self._warned_vision = True
                self._emit(
                    self._emit_sink,
                    {
                        "type": "warning",
                        "message": "附件没有发出去：当前模型被判定为不支持看图（vision 关）。"
                        "在「提供商 → 视觉」里打开，或换 gpt-4o / *-vl / gemini 这类模型。",
                    },
                )
            return (
                str(message.get("content") or "")
                + "\n\n[附件没能发给模型：当前模型不支持图片输入，只能当文件路径给我]"
            )
        if self.vision and attachments:
            parts: list[dict[str, Any]] = [{"type": "text", "text": str(message.get("content") or "")}]
            for item in attachments[:3]:
                rel = str(item.get("path") if isinstance(item, dict) else item or "")
                if not rel:
                    continue
                suffix = Path(rel).suffix.lower()
                if suffix not in _MIME:
                    continue
                target = Path(self.settings.sandbox_root) / rel
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

    def _payload(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": self.session.system_prompt}]
        for message in _trim_for_model(self.messages, self.settings.history_limit):
            clean = {k: v for k, v in message.items() if k not in {"reasoning", "attachments"}}
            if clean.get("role") == "user":
                clean["content"] = self._parts_for(message)
            elif isinstance(clean.get("content"), list):
                clean["content"] = json.dumps(clean["content"], ensure_ascii=False)
            out.append(clean)
        return out

    def _model_step(self, emit: Emit | None, tools: list[dict[str, Any]], turn: int) -> dict[str, Any]:
        payload = self._payload()
        if emit is None or not hasattr(self.runtime.llm, "stream_chat"):
            return self.runtime.llm.chat(payload, tools=tools)

        def forward(event: dict[str, Any]) -> None:
            kind = event.get("type")
            if kind == "reasoning_delta":
                self._emit(emit, {"type": "reasoning_delta", "turn": turn, "text": event.get("text", "")})
            elif kind == "content_delta":
                self._emit(emit, {"type": "content_delta", "turn": turn, "text": event.get("text", "")})

        return self.runtime.llm.stream_chat(payload, tools=tools, on_event=forward)

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
