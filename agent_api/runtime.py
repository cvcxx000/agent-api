"""运行时容器：把配置、模型、会话、工具、技能、工作流装到一起。"""

from __future__ import annotations

import logging
from typing import Any

from .config import Settings, settings as default_settings
from .cookies import CookieStore
from .llm import build_llm
from .prompts import load_default_system_prompt
from .sessions import SessionStore, SubagentManager
from .skills import SkillLibrary
from .tools import ToolRegistry, build_registry
from .workflows import WorkflowEngine

logger = logging.getLogger("agent.runtime")


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.default_system_prompt: str = ""
        self.llm: Any = None
        self.sessions: Any = None
        self.subagents: Any = None
        self.skills: Any = None
        self.workflows: Any = None
        self.cookie_store: Any = None
        self.registry: ToolRegistry | None = None


def create_runtime(settings: Settings | None = None) -> Runtime:
    st = settings or default_settings
    for directory in (
        st.sandbox_root,
        st.sandbox_root / "media",
        st.sandbox_root / "uploads",
        st.data_dir,
        st.data_dir / "sessions",
        st.data_dir / "tmp",
        st.workflows_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    # 预置模板只是目录：启动时把「没 Key、也不是当前在用」的空壳清掉，
    # 避免一打开就是 11 家自带模型的假象。要哪家随时从目录里加回来。
    from .providers import prune_empty

    try:
        removed = prune_empty(st).get("removed") or []
        if removed:
            logger.info("清理空壳提供商 %d 个：%s", len(removed), "、".join(removed))
    except Exception:  # noqa: BLE001 - 配置坏了不该挡住启动
        logger.warning("清理空壳提供商失败", exc_info=True)

    runtime = Runtime(st)
    runtime.default_system_prompt = load_default_system_prompt(st)
    runtime.llm = build_llm(st)
    runtime.sessions = SessionStore(st, runtime.default_system_prompt)
    runtime.cookie_store = CookieStore(st.data_dir / "cookies.json")
    runtime.skills = SkillLibrary(st.skills_dirs)
    runtime.subagents = SubagentManager(runtime, st.max_subagents, st.max_subagent_depth)
    runtime.workflows = WorkflowEngine(runtime, st.workflows_dir)
    runtime.registry = build_registry(runtime)
    logger.info(
        "runtime ready | model=%s mock=%s sandbox=%s tools=%d subagents<=%d",
        st.llm_model,
        st.use_mock_llm,
        st.sandbox_root,
        len(runtime.registry.names()),
        st.max_subagents,
    )
    return runtime