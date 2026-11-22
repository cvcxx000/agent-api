"""小工具函数。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def truncate(text: str, limit: int, note: str = "内容过长已截断") -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n...[{note}：共 {len(text)} 字符，仅保留前 {limit} 字符]"


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
