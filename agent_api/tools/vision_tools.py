"""看图工具：view_image —— 把图片真的喂给模型看，而不是让 Python 去读像素。

工具返回里带 `VIEW_IMAGE: <绝对路径>`，agent 下一轮会把这几张图拼成
多模态 user 消息（image_url data:URL），模型于是能直接「看到」图。
"""

from __future__ import annotations

from typing import Any

from .. import providers as provider_store
from ..config import settings
from ..errors import AppError
from ..security import display_path, resolve_in_sandbox
from .base import ToolContext, ToolRegistry

_MIME = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_BYTES = 4 * 1024 * 1024


def register(registry: ToolRegistry) -> None:
    def view_image(ctx: ToolContext, args: dict[str, Any]) -> str:
        raw = str(args.get("path") or "").strip()
        if not raw:
            raise AppError("path 不能为空（相对 workspace 或绝对路径都行）")
        path = resolve_in_sandbox(raw)
        if not path.exists() or not path.is_file():
            raise AppError(f"图片不存在：{display_path(path)}")
        if path.suffix.lower() not in _MIME:
            raise AppError(f"只支持 png / jpg / jpeg / webp / gif，这个是 {path.suffix or '无后缀'}")
        size = path.stat().st_size
        if size > _MAX_BYTES:
            raise AppError(f"图片 {size / 1024 / 1024:.1f} MB，太大了：先压到 4MB 以内再看")

        try:
            can_see = bool(provider_store.active_meta(settings).get("vision"))
        except Exception:  # noqa: BLE001 - 读不到配置就按能看处理
            can_see = True
        if not can_see:
            return (
                f"当前模型被判定为不支持看图，这张图没法给模型看：{display_path(path)}\n"
                "去「提供商 → 这个模型能读图片（多模态）：开」打开它，或换成 gpt-4o / *-vl / gemini 再试。"
            )

        note = str(args.get("note") or "").strip()
        head = f"VIEW_IMAGE: {path.resolve()}"
        if note:
            head += f"\n看图要求：{note}"
        return head + f"\n{display_path(path)}（{size / 1024:.0f} KB）已挂到下一轮上下文，模型会直接看到这张图。"

    registry.register(
        "view_image",
        "看一张图片：把图片直接喂给多模态模型（png/jpg/webp/gif，≤4MB）。"
        "凡是「这张图是什么 / 图里写了什么 / 对照图片」这类活，必须用这个工具；"
        "不要用 run_python 读像素或写 OCR 去猜，那是看不见图的。",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "图片路径，相对 workspace 或绝对路径"},
                "note": {"type": "string", "description": "可选：看图时要重点看什么（例如「读出左上角的型号」）"},
            },
            "required": ["path"],
        },
        view_image,
    )
