"""部署模式：personal（个人自用）/ merchant（商家订阅）/ unset（还没选）。

优先读环境变量 DEPLOY_MODE；没有就用 data/deploy.json（第一次启动时由页面写入）。
unset 按个人版的保守规则跑：只允许一个 owner、注册关闭，避免地址一暴露就谁都能注册。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import settings

MODES = ("personal", "merchant", "unset")
_FILE = Path(settings.data_dir) / "deploy.json"


def _from_file() -> str:
    try:
        raw = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unset"
    mode = str(raw.get("mode") or "unset").strip().lower()
    return mode if mode in MODES else "unset"


def mode() -> str:
    env = (os.getenv("DEPLOY_MODE") or "").strip().lower()
    if env in ("personal", "merchant"):
        return env
    return _from_file()


def env_locked() -> bool:
    return (os.getenv("DEPLOY_MODE") or "").strip().lower() in ("personal", "merchant")


def is_merchant() -> bool:
    return mode() == "merchant"


def status() -> dict[str, Any]:
    return {"mode": mode(), "env_locked": env_locked(), "file": str(_FILE)}


def reset() -> None:
    try:
        _FILE.unlink()
    except OSError:
        pass


def set_mode(value: str) -> str:
    mode_value = str(value or "").strip().lower()
    if mode_value not in ("personal", "merchant"):
        raise ValueError("mode 只能是 personal 或 merchant")
    if env_locked():
        raise ValueError("DEPLOY_MODE 写在环境变量里了，改 .env 再重启")
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"mode": mode_value}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_FILE)
    return mode_value
