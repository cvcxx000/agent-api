"""TTS 工具：让模型能自己「说」出来一段话。"""

from __future__ import annotations

from typing import Any

from ..errors import AppError
from ..net import NetError
from .base import ToolContext, ToolRegistry


def register(registry: ToolRegistry, runtime: Any) -> None:
    def speak_text(ctx: ToolContext, args: dict[str, Any]) -> str:
        from .. import tts as tts_store

        text = str(args.get("text") or "").strip()
        if not text:
            return "ERROR: text 不能为空"
        try:
            result = tts_store.speak(
                runtime.settings,
                text,
                voice=str(args.get("voice") or ""),
                speed=args.get("speed"),
                fmt=str(args.get("format") or ""),
            )
        except AppError as exc:
            return f"ERROR: {exc}"
        except NetError as exc:  # 兜底，模块内部已经包了一层
            return f"ERROR: TTS 请求失败：{exc}"
        except Exception as exc:  # noqa: BLE001 - 工具不能把异常抛给循环
            return f"ERROR: 语音合成失败：{type(exc).__name__}: {exc}"
        return (
            f"MEDIA_AUDIO: {result['url']}\n"
            f"已合成 {result['text_chars']} 字，{result['bytes']} 字节，"
            f"模型 {result['model']}，音色 {result['voice'] or '默认'}，"
            f"文件 {result['path']}"
        )

    registry.register(
        "speak_text",
        "把一段文字合成语音（TTS），返回 MEDIA_AUDIO 地址，网页/终端里能直接播。"
        "适合做朗读、配音、语音播报；需要先在设置里配置语音合成（Base URL + Key + 模型）。",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要朗读的文字，最长 4096 字"},
                "voice": {"type": "string", "description": "音色，留空用配置里的默认（如 alloy / FunAudioLLM/CosyVoice2-0.5B:alex）"},
                "speed": {"type": "number", "description": "语速 0.25 ~ 4.0，留空用默认"},
                "format": {"type": "string", "description": "mp3 / wav / opus / aac / flac，留空用默认"},
            },
            "required": ["text"],
        },
        speak_text,
    )
