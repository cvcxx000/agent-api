"""把各类工具装配进注册表。"""

from __future__ import annotations

from typing import Any

from . import (
    agent_tools,
    cookie_tools,
    fs_tools,
    media_tools,
    plugin_tools,
    py_tools,
    skill_tools,
    video_tools,
    vision_tools,
    web_tools,
    workflow_tools,
)
from .base import ToolContext, ToolRegistry, ToolSpec

__all__ = ["ToolRegistry", "ToolContext", "ToolSpec", "build_registry"]


def build_registry(runtime: Any) -> ToolRegistry:
    registry = ToolRegistry()
    fs_tools.register(registry)
    py_tools.register(registry)
    cookie_tools.register(registry, lambda: runtime.cookie_store)
    web_tools.register(registry)
    media_tools.register(registry)
    video_tools.register(registry)
    vision_tools.register(registry)
    skill_tools.register(registry, lambda: runtime.skills)
    plugin_tools.register(registry, lambda: runtime.plugins)
    agent_tools.register(registry, lambda: runtime)
    workflow_tools.register(registry, lambda: runtime.workflows)

    # 插件最后装：坏插件只影响自己，不挡住其他工具
    from ..plugins import load_enabled

    load_enabled(runtime.plugins, registry)
    return registry
