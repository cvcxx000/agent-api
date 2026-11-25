"""运行时开关：不重启也能改的少量策略（目前只有游客试用）。

环境变量写了就以环境变量为准；没写才看 data/policy.json。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .config import settings

_FILE = Path(settings.data_dir) / "policy.json"
_LOCK = threading.RLock()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


def _env_bool(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return None
    return raw not in {"0", "false", "off", "no"}


def guest_enabled() -> bool:
    env = _env_bool("GUEST_ENABLED")
    if env is not None:
        return env
    with _LOCK:
        value = _load().get("guest_enabled")
    return True if value is None else bool(value)


def set_guest_enabled(value: bool) -> None:
    with _LOCK:
        data = _load()
        data["guest_enabled"] = bool(value)
        _save(data)


def reset() -> None:
    with _LOCK:
        try:
            _FILE.unlink()
        except OSError:
            pass
