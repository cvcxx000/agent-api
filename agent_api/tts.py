"""语音合成（TTS）：配置存 data/tts.json，调用走 OpenAI 兼容的 /audio/speech。

只走「真的能出声」那条路径：填 Base URL + Key + 模型 + 音色 → 后端拿音频字节 →
落到 workspace/media/ → 界面用 /files/media/... 直接播。

为什么单独一个模块而不是塞进 media.py：TTS 的请求形状不是 JSON 响应，是直接
回二进制音频，且音色/语速/格式这些参数跟图片视频完全不同；混在一起以后改一处
容易顺手崩另一处。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from .security import active_root
from typing import Any

from .config import Settings
from .errors import AppError
from .net import NetError, bearer, post_raw

# 预置只是「地址 + 模型 + 音色」的模板，不预置假 Key，也不假装能出声。
PRESETS: list[dict[str, Any]] = [
    {
        "id": "openai",
        "name": "OpenAI TTS",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "format": "mp3",
        "note": "官方 /audio/speech，音色有 alloy、echo、fable、onyx、nova、shimmer 等",
    },
    {
        "id": "siliconflow",
        "name": "硅基流动 CosyVoice",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:alex",
        "format": "mp3",
        "note": "音色写成 模型:说话人，例如 alex / bella；先拉模型再挑音色",
    },
    {
        "id": "custom",
        "name": "自定义（OpenAI 兼容 /audio/speech）",
        "base_url": "",
        "model": "",
        "voice": "",
        "format": "mp3",
        "note": "任何实现 POST /audio/speech 的服务都能填，字段名按 OpenAI 形状发",
    },
]

_FORMATS = {"mp3", "wav", "opus", "aac", "flac"}

_DEFAULTS: dict[str, Any] = {
    "preset": "",
    "name": "",
    "base_url": "",
    "api_key": "",
    "model": "",
    "voice": "",
    "format": "mp3",
    "speed": 1.0,
    "autoplay": True,
}


def _path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "tts.json"


def load(settings: Settings) -> dict[str, Any]:
    path = _path(settings)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError):
            data = {}
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def save(settings: Settings, data: dict[str, Any]) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def preset_by_id(preset_id: str) -> dict[str, Any] | None:
    for item in PRESETS:
        if item["id"] == preset_id:
            return item
    return None


def _tail(key: str) -> str:
    key = str(key or "")
    if not key:
        return ""
    return key[-4:] if len(key) > 8 else "…"


def public(settings: Settings) -> dict[str, Any]:
    cfg = load(settings)
    return {
        "preset": cfg.get("preset", ""),
        "name": cfg.get("name", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "voice": cfg.get("voice", ""),
        "format": cfg.get("format", "mp3"),
        "speed": cfg.get("speed", 1.0),
        "autoplay": bool(cfg.get("autoplay", True)),
        "has_key": bool(cfg.get("api_key")),
        "key_tail": _tail(cfg.get("api_key", "")),
        "configured": bool(cfg.get("base_url") and cfg.get("model")),
        "presets": PRESETS,
    }


def configure(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    cfg = load(settings)

    preset_id = str(body.get("preset") or "").strip()
    if preset_id:
        preset = preset_by_id(preset_id)
        if preset is None:
            raise AppError(f"没有这个 TTS 预置：{preset_id}")
        cfg["preset"] = preset["id"]
        cfg["name"] = preset["name"]
        cfg["base_url"] = (preset.get("base_url") or cfg.get("base_url") or "").rstrip("/")
        cfg["model"] = preset.get("model") or cfg.get("model", "")
        cfg["voice"] = preset.get("voice") or cfg.get("voice", "")
        cfg["format"] = preset.get("format") or cfg.get("format", "mp3")

    for field in ("base_url", "model", "voice", "format", "name"):
        value = body.get(field)
        if isinstance(value, str) and value.strip():
            cfg[field] = value.strip().rstrip("/") if field == "base_url" else value.strip()

    if cfg.get("format") not in _FORMATS:
        raise AppError(f"format 只能是 {' / '.join(sorted(_FORMATS))}")

    speed = body.get("speed")
    if speed is not None and str(speed).strip():
        try:
            cfg["speed"] = max(0.25, min(4.0, float(speed)))
        except ValueError:
            raise AppError("speed 必须是数字（0.25 ~ 4.0）") from None

    if "autoplay" in body:
        cfg["autoplay"] = bool(body.get("autoplay"))

    key = body.get("api_key")
    if isinstance(key, str) and key.strip():
        cfg["api_key"] = key.strip()
    if body.get("clear_key") is True:
        cfg["api_key"] = ""

    save(settings, cfg)
    return public(settings)


def _media_dir(settings: Settings) -> Path:
    path = active_root() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_audio(settings: Settings, payload: bytes, fmt: str) -> dict[str, Any]:
    if not payload:
        raise AppError("TTS 返回了空音频：检查模型名和音色是否正确")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"tts-{stamp}-{int(time.time() * 1000) % 1000:03d}.{fmt}"
    target = _media_dir(settings) / name
    target.write_bytes(payload)
    rel = target.relative_to(active_root()).as_posix()
    return {"path": rel, "url": "/files/" + rel, "bytes": len(payload), "name": name}


def speak(
    settings: Settings,
    text: str,
    voice: str = "",
    speed: float | None = None,
    fmt: str = "",
) -> dict[str, Any]:
    """把一段文字合成语音，返回 {path, url, bytes, voice, model}。"""
    text = str(text or "").strip()
    if not text:
        raise AppError("text 不能为空")
    if len(text) > 4096:
        raise AppError(f"一次最多 4096 个字符（现在 {len(text)} 个），长文请分段")

    cfg = load(settings)
    if not cfg.get("base_url") or not cfg.get("model"):
        raise AppError("还没配置 TTS：设置 → 语音合成，选预置、填 Key 和模型")

    chosen_voice = (voice or cfg.get("voice") or "").strip()
    chosen_fmt = (fmt or cfg.get("format") or "mp3").strip()
    if chosen_fmt not in _FORMATS:
        chosen_fmt = "mp3"
    chosen_speed = float(speed if speed is not None else cfg.get("speed") or 1.0)

    url = cfg["base_url"].rstrip("/") + "/audio/speech"
    body: dict[str, Any] = {
        "model": cfg["model"],
        "input": text,
        "response_format": chosen_fmt,
        "speed": chosen_speed,
    }
    if chosen_voice:
        body["voice"] = chosen_voice

    try:
        audio, content_type = post_raw(url, body, bearer(cfg.get("api_key", "")), timeout=120)
    except NetError as exc:
        raise AppError(f"TTS 请求失败（{url}）：{exc}") from exc

    # 有些中转站出错也回 200 + JSON，这里挡一下，别把错误当音频存下来。
    head = audio[:64].lstrip()
    if content_type and "json" in content_type.lower():
        raise AppError("TTS 返回的是 JSON 而不是音频：" + audio.decode("utf-8", "replace")[:300])
    if head.startswith(b"{") or head.startswith(b"["):
        raise AppError("TTS 返回的是 JSON 而不是音频：" + audio.decode("utf-8", "replace")[:300])

    saved = _save_audio(settings, audio, chosen_fmt)
    return {
        **saved,
        "voice": chosen_voice,
        "model": cfg["model"],
        "speed": chosen_speed,
        "text_chars": len(text),
    }
