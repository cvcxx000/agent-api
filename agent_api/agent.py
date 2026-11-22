"""Agent 主循环：模型 → 工具 → 观察 → 再模型，直到给出最终答复（同步实现）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import AgentCancelled, LLMError
from .sessions import Session
from .tools.base import ToolContext

logger = logging.getLogger("agent.core")

Emit = Callable[[dict[str, Any]], None]


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

    def _payload(self) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.session.system_prompt}] + _trim_for_model(
            self.messages, self.settings.history_limit
        )

    def run(self, user_text: str, emit: Emit | None = None) -> AgentResult:
        steps: list[dict[str, Any]] = []
        self.messages.append({"role": "user", "content": user_text})
        self._emit(emit, {"type": "user", "content": user_text})

        final_text = ""
        turns = 0
        for turn in range(self.max_turns):
            self._check_cancel()
            turns = turn + 1
            tools = self.runtime.registry.openai_specs(self.ctx)
            try:
                message = self.runtime.llm.chat(self._payload(), tools=tools)
            except LLMError as exc:
                self._emit(emit, {"type": "error", "message": exc.message})
                raise

            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.messages.append(assistant_msg)

            if content:
                steps.append({"type": "assistant", "turn": turns, "content": content})
                self._emit(emit, {"type": "assistant", "turn": turns, "content": content})

            if not tool_calls:
                final_text = content
                break

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

                self.messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name, "content": result})
                steps.append(
                    {"type": "tool", "turn": turns, "id": call.get("id"), "name": name, "arguments": arguments, "output": result}
                )
                self._emit(
                    emit, {"type": "tool_result", "turn": turns, "id": call.get("id"), "name": name, "output": result}
                )
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
