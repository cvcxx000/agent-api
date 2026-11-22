"""系统提示词加载：默认读 prompts/default_system.md，可用环境变量覆盖。"""

from __future__ import annotations

import os
from pathlib import Path

from .config import Settings

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "default_system.md"


def load_default_system_prompt(settings: Settings) -> str:
    override = os.getenv("SYSTEM_PROMPT", "").strip()
    file_override = os.getenv("SYSTEM_PROMPT_FILE", "").strip()
    if not override and file_override:
        try:
            override = Path(file_override).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"读取 SYSTEM_PROMPT_FILE 失败：{exc}") from exc
    if not override:
        override = PROMPT_FILE.read_text(encoding="utf-8")
    return override.replace("{sandbox_root}", str(settings.sandbox_root))
