"""把各类工具装配进注册表。"""

from __future__ import annotations

from typing import Any

from . import agent_tools, cookie_tools, fs_tools, media_tools, py_tools, skill_tools, web_tools, workflow_tools
from .base import ToolContext, ToolRegistry, ToolSpec

__all__ = ["ToolRegistry", "ToolContext", "ToolSpec", "build_registry"]


def build_registry(runtime: Any) -> ToolRegistry:
    registry = ToolRegistry()
    fs_tools.register(registry)
    py_tools.register(registry)
    cookie_tools.register(registry, lambda: runtime.cookie_store)
    web_tools.register(registry)
    media_tools.register(registry)
    skill_tools.register(registry, lambda: runtime.skills)
    agent_tools.register(registry, lambda: runtime)
    workflow_tools.register(registry, lambda: runtime.workflows)
    return registry