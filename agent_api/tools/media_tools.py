"""媒体生成工具：generate_image / generate_video。

配置在 data/media.json（界面：设置 → 图片生成 / 视频生成）。生成的字节落到
workspace/media/，返回里带 /files/media/... 地址，网页和终端都能直接看。
"""

from __future__ import annotations

from typing import Any

from ..config import settings
from ..errors import AppError
from ..media import generate_image, generate_video
from .base import ToolContext, ToolRegistry

_IMAGE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "画面描述，越具体越好（主体、风格、光线、构图）"},
        "size": {"type": "string", "description": "可选，例如 1024x1024 / 1024x1792；留空用配置里的默认"},
    },
    "required": ["prompt"],
}

_VIDEO_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "镜头描述：主体动作 + 运镜 + 风格"},
        "seconds": {"type": "integer", "description": "时长秒数，1-60，默认用配置里的值"},
        "timeout": {"type": "integer", "description": "等待出片的最长秒数，默认 420"},
    },
    "required": ["prompt"],
}


def _fmt_image(result: dict[str, Any]) -> str:
    kb = result.get("bytes", 0) / 1024
    return (
        f"图片已生成：{result['path']}（{kb:.0f} KB）\n"
        f"模型：{result.get('model', '')}\n"
        f"MEDIA_IMAGE: {result['url']}\n"
        f"![生成的图片]({result['url']})\n"
    )


def _fmt_video(result: dict[str, Any]) -> str:
    kb = result.get("bytes", 0) / 1024
    return (
        f"视频已生成：{result['path']}（{kb:.0f} KB，{result.get('seconds', '?')}s）\n"
        f"模型：{result.get('model', '')}  模式：{result.get('mode', '')}\n"
        f"MEDIA_VIDEO: {result['url']}\n"
    )


def register(registry: ToolRegistry) -> None:
    def image(ctx: ToolContext, args: dict[str, Any]) -> str:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise AppError("prompt 不能为空")
        result = generate_image(settings, prompt, str(args.get("size") or ""))
        return _fmt_image(result)

    def video(ctx: ToolContext, args: dict[str, Any]) -> str:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise AppError("prompt 不能为空")
        seconds = args.get("seconds")
        timeout = args.get("timeout")
        result = generate_video(
            settings,
            prompt,
            int(seconds) if seconds not in (None, "") else None,
            timeout=int(timeout) if timeout not in (None, "") else 420,
        )
        return _fmt_video(result)

    registry.register(
        "generate_image",
        "按文字描述生成一张图片，存到 workspace/media/ 并返回可访问地址。"
        "需要先在「设置 → 图片生成」里配好 Base URL、Key 和模型。",
        _IMAGE_PARAMS,
        image,
    )
    registry.register(
        "generate_video",
        "按文字描述生成一段短视频（提交任务 → 轮询 → 下载 mp4），存到 workspace/media/。"
        "耗时通常几十秒到几分钟；需要先在「设置 → 视频生成」里配好服务。",
        _VIDEO_PARAMS,
        video,
    )
