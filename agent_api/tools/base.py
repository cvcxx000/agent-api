"""工具注册表：统一 OpenAI function-calling schema。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import settings
from ..errors import AppError, NotFoundError
from ..utils import truncate

logger = logging.getLogger("agent.tools")


@dataclass
class ToolContext:
    """一次工具调用所处的上下文（哪个会话、第几层 Agent）。"""

    session: Any
    agent_id: str = "main"
    depth: int = 0
    allow_spawn: bool = True
    parent_session_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[ToolContext, dict[str, Any]], str]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    requires_spawn: bool = False


GUEST_BLOCKED_PREFIXES = ("spawn_", "wait_", "cancel_", "plugin_", "schedule_", "cookie_")
GUEST_BLOCKED_NAMES = {"spawn_agent", "wait_agent", "list_agents", "cancel_agent"}
GUEST_UNLOCK_NAMES = {"run_python", "run_shell"}


def guest_tool_error(name: str, ctx: ToolContext) -> str:
    """游客工具闸门：返回空串=放行，否则返回给模型看的 ERROR。"""
    if not ctx.extra.get("guest"):
        return ""
    if name in GUEST_BLOCKED_NAMES or name.startswith(GUEST_BLOCKED_PREFIXES):
        return f"ERROR: 游客不能用工具 {name}：子 Agent / 插件 / 定时任务 / Cookie 都属于正式账号"
    if name in GUEST_UNLOCK_NAMES and not ctx.extra.get("guest_unlocked"):
        return "ERROR: 游客用 Python / 终端要先解锁：开游客时给过一串解锁码，在账号面板或终端 /unlock 输入后才行（仍然只能动自己的目录）"
    return ""


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Handler,
        requires_spawn: bool = False,
    ) -> None:
        if name in self._specs:
            raise ValueError(f"重复注册的工具：{name}")
        self._specs[name] = ToolSpec(name, description, parameters, handler, requires_spawn)

    def names(self) -> list[str]:
        return list(self._specs)

    def unregister(self, name: str) -> bool:
        """摘掉一个工具（插件热重载/停用时用）。"""
        return self._specs.pop(name, None) is not None

    def get(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise NotFoundError(f"未知工具：{name}")
        return spec

    def available(self, ctx: ToolContext) -> list[ToolSpec]:
        out: list[ToolSpec] = []
        for spec in self._specs.values():
            if spec.requires_spawn and not ctx.allow_spawn:
                continue
            if guest_tool_error(spec.name, ctx):
                continue
            out.append(spec)
        return out

    def openai_specs(self, ctx: ToolContext) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters},
            }
            for spec in self.available(ctx)
        ]

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "description": s.description, "parameters": s.parameters} for s in self._specs.values()]

    def execute(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> str:
        spec = self._specs.get(name)
        if spec is None:
            return f"ERROR: 未知工具 {name}，可用工具：{', '.join(self._specs)}"
        if spec.requires_spawn and not ctx.allow_spawn:
            return f"ERROR: 当前 Agent 层级（depth={ctx.depth}）不允许再派发子 Agent。"
        blocked = guest_tool_error(name, ctx)
        if blocked:
            return blocked
        try:
            output = spec.handler(ctx, arguments or {})
            text = output if isinstance(output, str) else str(output)
        except AppError as exc:
            logger.info("工具 %s 失败：%s", name, exc.message)
            return f"ERROR: {exc.message}"
        except Exception as exc:  # noqa: BLE001
            logger.exception("工具 %s 异常", name)
            return f"ERROR: {type(exc).__name__}: {exc}"
        return truncate(text, settings.max_output_chars)
