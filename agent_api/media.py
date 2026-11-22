"""图片 / 视频生成：配置存 data/media.json，调用走 OpenAI 兼容形状。

只做「能真的出东西」的那条路径：填 Base URL + Key + 模型 → 后端拿字节 → 落到
workspace/media/ → 界面用 /files/media/... 直接看。不同中转站字段不一时，
错误正文会原样带回来，不会假装成功。
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import AppError
from .net import NetError, bearer, find_url, get_bytes, get_json, pick, post_json
from .presets import media_preset, media_presets

_KINDS = ("image", "video")

_DEFAULTS: dict[str, dict[str, Any]] = {
    "image": {
        "preset": "",
        "name": "",
        "base_url": "",
        "api_key": "",
        "model": "",
        "mode": "openai",
        "size": "1024x1024",
    },
    "video": {
        "preset": "",
        "name": "",
        "base_url": "",
        "api_key": "",
        "model": "",
        "mode": "openai_video",
        "size": "720x1280",
        "seconds": 5,
    },
}


def _path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "media.json"


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
    for kind in _KINDS:
        merged = dict(_DEFAULTS[kind])
        merged.update({k: v for k, v in (data.get(kind) or {}).items() if v is not None})
        data[kind] = merged
    return data


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


def _tail(key: str) -> str:
    key = str(key or "")
    if not key:
        return ""
    return key[-4:] if len(key) > 8 else "…"


def public(settings: Settings) -> dict[str, Any]:
    data = load(settings)
    out: dict[str, Any] = {}
    for kind in _KINDS:
        cfg = data[kind]
        out[kind] = {
            "preset": cfg.get("preset", ""),
            "name": cfg.get("name", ""),
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "mode": cfg.get("mode", ""),
            "size": cfg.get("size", ""),
            "seconds": cfg.get("seconds", 0),
            "has_key": bool(cfg.get("api_key")),
            "key_tail": _tail(cfg.get("api_key", "")),
            "ready": bool(cfg.get("base_url") and cfg.get("model")),
        }
        out[kind]["presets"] = media_presets(kind)
    return out


def _apply_preset(cfg: dict[str, Any], preset: dict[str, Any]) -> None:
    cfg["preset"] = preset["id"]
    cfg["name"] = preset["name"]
    cfg["base_url"] = (preset.get("base_url") or cfg.get("base_url") or "").rstrip("/")
    cfg["model"] = preset.get("model") or cfg.get("model", "")
    cfg["mode"] = preset.get("mode") or cfg.get("mode", "")


def configure(settings: Settings, kind: str, body: dict[str, Any]) -> dict[str, Any]:
    if kind not in _KINDS:
        raise AppError("kind 只能是 image 或 video")
    data = load(settings)
    cfg = data[kind]

    preset_id = str(body.get("preset") or "").strip()
    if preset_id:
        preset = media_preset(kind, preset_id)
        if preset is None:
            raise AppError(f"没有这个预置：{preset_id}")
        _apply_preset(cfg, preset)

    for field in ("base_url",):
        value = body.get(field)
        if isinstance(value, str) and value.strip():
            cfg[field] = value.strip().rstrip("/")
    for field in ("model", "size", "mode", "name"):
        value = body.get(field)
        if isinstance(value, str) and value.strip():
            cfg[field] = value.strip()
    if kind == "video":
        seconds = body.get("seconds")
        if seconds is not None and str(seconds).strip():
            try:
                cfg["seconds"] = max(1, min(60, int(float(seconds))))
            except ValueError:
                raise AppError("seconds 必须是数字") from None
    key = body.get("api_key")
    if isinstance(key, str) and key.strip():
        cfg["api_key"] = key.strip()
    if body.get("clear_key") is True:
        cfg["api_key"] = ""
    save(settings, data)
    return public(settings)


def _media_dir(settings: Settings) -> Path:
    path = Path(settings.sandbox_root) / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ext_from(content_type: str, hint: str) -> str:
    low = (content_type or "").lower()
    if "png" in low:
        return ".png"
    if "jpeg" in low or "jpg" in low:
        return ".jpg"
    if "webp" in low:
        return ".webp"
    if "mp4" in low:
        return ".mp4"
    if "webm" in low:
        return ".webm"
    return hint


def _save(settings: Settings, payload: bytes, ext: str, kind: str) -> dict[str, Any]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{kind}-{stamp}-{int(time.time() * 1000) % 1000:03d}{ext}"
    target = _media_dir(settings) / name
    target.write_bytes(payload)
    rel = target.relative_to(Path(settings.sandbox_root)).as_posix()
    return {"path": rel, "url": "/files/" + rel, "bytes": len(payload), "name": name}


def _config_or_error(settings: Settings, kind: str) -> dict[str, Any]:
    cfg = load(settings)[kind]
    if not cfg.get("base_url") or not cfg.get("model"):
        raise AppError(
            f"还没配置{('图片' if kind == 'image' else '视频')}生成：设置 → "
            f"{('图片生成' if kind == 'image' else '视频生成')}，选预置、填 Key 和模型"
        )
    return cfg


def generate_image(settings: Settings, prompt: str, size: str = "", model: str = "") -> dict[str, Any]:
    prompt = str(prompt or "").strip()
    if not prompt:
        raise AppError("prompt 不能为空")
    cfg = _config_or_error(settings, "image")
    url = cfg["base_url"].rstrip("/") + "/images/generations"
    headers = bearer(cfg.get("api_key", ""))
    body: dict[str, Any] = {
        "model": model.strip() or cfg["model"],
        "prompt": prompt,
        "n": 1,
        "size": size.strip() or cfg.get("size") or "1024x1024",
        "response_format": "b64_json",
    }
    try:
        data = post_json(url, body, headers, timeout=180)
    except NetError as exc:
        # 有些中转站不认 response_format，去掉再试一次
        if exc.status in (400, 422):
            body.pop("response_format", None)
            try:
                data = post_json(url, body, headers, timeout=180)
            except NetError as exc2:
                raise AppError(f"图片生成失败：{exc2}") from exc2
        else:
            raise AppError(f"图片生成失败：{exc}") from exc

    items = data.get("data") if isinstance(data.get("data"), list) else []
    if not items:
        raise AppError(f"图片接口没返回图：{json.dumps(data, ensure_ascii=False)[:400]}")
    item = items[0] if isinstance(items[0], dict) else {}
    raw = item.get("b64_json") or item.get("base64")
    if raw:
        try:
            payload = base64.b64decode(str(raw))
        except Exception as exc:  # noqa: BLE001
            raise AppError(f"返回的 b64 解不开：{exc}") from exc
        content_type = "image/png"
    else:
        link = pick(item, "url", "image_url") or find_url(data)
        if not link:
            raise AppError(f"图片接口没给地址：{json.dumps(data, ensure_ascii=False)[:400]}")
        try:
            payload, content_type = get_bytes(link, headers, timeout=180)
        except NetError as exc:
            raise AppError(f"图片下载失败：{exc}") from exc

    saved = _save(settings, payload, _ext_from(content_type, ".png"), "image")
    saved.update({"model": body["model"], "prompt": prompt, "revised_prompt": item.get("revised_prompt", "")})
    return saved


def _download_video(url: str, headers: dict[str, str], settings: Settings) -> dict[str, Any]:
    try:
        payload, content_type = get_bytes(url, headers, timeout=300)
    except NetError as exc:
        raise AppError(f"视频下载失败：{exc}") from exc
    if len(payload) < 1024:
        raise AppError(f"视频只有 {len(payload)} 字节，可能被服务商拒了")
    saved = _save(settings, payload, _ext_from(content_type, ".mp4"), "video")
    saved["source_url"] = url
    return saved


def _poll_openai_video(base: str, vid: str, headers: dict[str, str], settings: Settings,
                       deadline: float) -> dict[str, Any]:
    while True:
        try:
            state = get_json(f"{base}/videos/{vid}", headers, timeout=60)
        except NetError as exc:
            raise AppError(f"查询视频任务失败：{exc}") from exc
        status = str(state.get("status") or "").lower()
        if status in {"completed", "succeeded", "success"}:
            return _download_video(f"{base}/videos/{vid}/content", headers, settings)
        if status in {"failed", "error", "cancelled"}:
            raise AppError(f"视频生成失败：{json.dumps(state, ensure_ascii=False)[:400]}")
        if time.time() > deadline:
            raise AppError("视频生成超时（可调大 timeout 或稍后重试）")
        time.sleep(3)


def _poll_siliconflow(base: str, request_id: str, headers: dict[str, str], settings: Settings,
                      deadline: float) -> dict[str, Any]:
    while True:
        try:
            state = post_json(f"{base}/video/status", {"requestId": request_id}, headers, timeout=60)
        except NetError as exc:
            raise AppError(f"查询视频状态失败：{exc}") from exc
        status = str(state.get("status") or "").upper()
        if status in {"SUCCEED", "SUCCESS", "COMPLETED"}:
            link = find_url(state)
            if not link:
                raise AppError(f"任务成功但没给视频地址：{json.dumps(state, ensure_ascii=False)[:400]}")
            return _download_video(link, headers, settings)
        if status in {"FAILED", "FAIL", "ERROR"}:
            reason = state.get("reason") or state.get("message") or ""
            raise AppError(f"视频生成失败：{reason or status}")
        if time.time() > deadline:
            raise AppError("视频生成超时（可调大 timeout 或稍后重试）")
        time.sleep(4)


def generate_video(settings: Settings, prompt: str, seconds: int | None = None, model: str = "",
                   timeout: int = 420) -> dict[str, Any]:
    prompt = str(prompt or "").strip()
    if not prompt:
        raise AppError("prompt 不能为空")
    cfg = _config_or_error(settings, "video")
    base = cfg["base_url"].rstrip("/")
    headers = bearer(cfg.get("api_key", ""))
    mode = str(cfg.get("mode") or "openai_video")
    chosen = model.strip() or cfg["model"]
    secs = max(1, min(60, int(seconds or cfg.get("seconds") or 5)))
    deadline = time.time() + max(30, timeout)

    if mode == "siliconflow_video":
        try:
            submit = post_json(f"{base}/video/submit", {"model": chosen, "prompt": prompt}, headers, timeout=90)
        except NetError as exc:
            raise AppError(f"提交视频任务失败：{exc}") from exc
        rid = pick(submit, "requestId", "request_id", "id", "task_id")
        if not rid:
            raise AppError(f"没拿到 requestId：{json.dumps(submit, ensure_ascii=False)[:400]}")
        saved = _poll_siliconflow(base, rid, headers, settings, deadline)
        saved.update({"mode": mode, "model": chosen, "prompt": prompt, "seconds": secs, "task_id": rid})
        return saved

    if mode == "generic_video":
        try:
            submit = post_json(f"{base}/videos/generations", {"model": chosen, "prompt": prompt, "seconds": secs}, headers, timeout=90)
        except NetError as exc:
            raise AppError(f"提交视频任务失败：{exc}") from exc
        direct = find_url(submit)
        if direct:
            saved = _download_video(direct, headers, settings)
            saved.update({"mode": mode, "model": chosen, "prompt": prompt, "seconds": secs})
            return saved
        rid = pick(submit, "task_id", "requestId", "request_id", "id")
        if not rid:
            raise AppError(f"没拿到任务 id：{json.dumps(submit, ensure_ascii=False)[:400]}")
        status_url = pick(submit, "status_url", "poll_url") or f"{base}/videos/generations/{rid}"
        while True:
            try:
                state = get_json(status_url, headers, timeout=60)
            except NetError as exc:
                raise AppError(f"查询视频任务失败：{exc}") from exc
            link = find_url(state)
            status = str(state.get("status") or "").lower()
            if link and status in {"succeeded", "completed", "success", "done", ""}:
                saved = _download_video(link, headers, settings)
                saved.update({"mode": mode, "model": chosen, "prompt": prompt, "seconds": secs, "task_id": rid})
                return saved
            if status in {"failed", "error"}:
                raise AppError(f"视频生成失败：{json.dumps(state, ensure_ascii=False)[:400]}")
            if time.time() > deadline:
                raise AppError("视频生成超时")
            time.sleep(4)

    # 默认：OpenAI 形状
    try:
        submit = post_json(
            f"{base}/videos",
            {"model": chosen, "prompt": prompt, "seconds": str(secs), "size": cfg.get("size") or "720x1280"},
            headers,
            timeout=90,
        )
    except NetError as exc:
        raise AppError(f"提交视频任务失败：{exc}") from exc
    vid = pick(submit, "id", "task_id", "requestId")
    if not vid:
        direct = find_url(submit)
        if direct:
            saved = _download_video(direct, headers, settings)
            saved.update({"mode": mode, "model": chosen, "prompt": prompt, "seconds": secs})
            return saved
        raise AppError(f"没拿到视频任务 id：{json.dumps(submit, ensure_ascii=False)[:400]}")
    saved = _poll_openai_video(base, vid, headers, settings, deadline)
    saved.update({"mode": mode, "model": chosen, "prompt": prompt, "seconds": secs, "task_id": vid})
    return saved
