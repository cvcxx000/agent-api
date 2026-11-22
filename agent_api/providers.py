"""多提供商配置：所有 Base URL / Key / 模型列表统一存在 data/providers.json。

一个文件管住全部厂商，界面里「提供商」列表就是它的投影；切换只改内存 settings，
不写 .env，重启后仍是 .env 里那一套（除非你手动改 .env）。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import AppError, NotFoundError
from .net import NetError, bearer, get_json
from .presets import llm_catalog, llm_preset, preset_ids

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}

# 预置只当模板：目录在 presets.py，这里不再默认塞一堆没有 Key 的空壳。
DEFAULTS = llm_catalog()


def _store_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "providers.json"


def _slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")
    return out or "custom"


def _tail(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) > 8:
        return key[-4:]
    return "**" + key[-2:]


def _seed(settings: Settings) -> dict[str, Any]:
    """新装时只放一条：.env 里那套配置（有 Key 才算数），其余一律留空。

    界面上「添加提供商」从目录挑，挑完填 Key、拉模型，不再凭空出现 11 个空壳。
    """
    providers: list[dict[str, Any]] = []
    base = (settings.llm_base_url or "").rstrip("/")
    model = (settings.llm_model or "").strip()
    key = (settings.llm_api_key or "").strip()
    active = {"provider": "", "model": ""}

    if key:
        for item in DEFAULTS:
            if not base or item["base_url"].rstrip("/") != base:
                continue
            provider = {
                "id": item["id"],
                "name": item["name"],
                "notes": item.get("notes", ""),
                "base_url": item["base_url"].rstrip("/"),
                "api_key": key,
                "enabled": True,
                "models": [model] if model else [],
                "vision": False,
                "preset": True,
            }
            providers.append(provider)
            active = {"provider": provider["id"], "model": model}
            break

    if not providers:
        # .env 没 Key：一条都不放，界面显示「还没有提供商」，从目录加。
        active = {"provider": "", "model": ""}
    return {"active": active, "providers": providers}



def load(settings: Settings) -> dict[str, Any]:
    path = _store_path(settings)
    key = str(path)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AppError(f"providers.json 读取失败：{exc}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("providers"), list):
                raise AppError("providers.json 格式不对，缺少 providers 数组")
            data.setdefault("active", {"provider": "", "model": ""})
            known = preset_ids()
            for provider in data["providers"]:
                provider.setdefault("api_key", "")
                provider.setdefault("enabled", False)
                provider.setdefault("models", [])
                provider.setdefault("notes", "")
                provider.setdefault("vision", False)
                provider.setdefault("preset", provider.get("id") in known)
            _cache[key] = data
            return data
        data = _seed(settings)
        _save(path, data)
        _cache[key] = data
        return data


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save(settings: Settings, data: dict[str, Any]) -> None:
    path = _store_path(settings)
    with _lock:
        _save(path, data)
        _cache[str(path)] = data


def public(settings: Settings) -> dict[str, Any]:
    """给界面看的视图：不带明文 Key，只给尾号和是否已配置。"""
    data = load(settings)
    active = data.get("active") or {}
    items = []
    for provider in data["providers"]:
        items.append(
            {
                "id": provider["id"],
                "name": provider["name"],
                "notes": provider.get("notes", ""),
                "base_url": provider["base_url"],
                "enabled": bool(provider.get("enabled")),
                "models": list(provider.get("models") or []),
                "model_count": len(provider.get("models") or []),
                "has_key": bool(provider.get("api_key")),
                "key_tail": _tail(provider.get("api_key", "")),
                "is_active": provider["id"] == active.get("provider"),
                "active_model": active.get("model", "") if provider["id"] == active.get("provider") else "",
                "vision": bool(provider.get("vision")),
                "is_preset": bool(provider.get("preset")),
            }
        )
    return {
        "active": {"provider": active.get("provider", ""), "model": active.get("model", "")},
        "providers": items,
        "store": str(_store_path(settings)),
    }


def _find(data: dict[str, Any], provider_id: str) -> dict[str, Any]:
    for provider in data["providers"]:
        if provider["id"] == provider_id:
            return provider
    raise NotFoundError(f"没有这个提供商：{provider_id}")


def upsert(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    data = load(settings)
    provider_id = str(body.get("id") or "").strip()
    is_new = not provider_id
    if is_new:
        base_id = _slug(body.get("name") or "custom")
        provider_id = base_id
        n = 2
        while any(p["id"] == provider_id for p in data["providers"]):
            provider_id = f"{base_id}-{n}"
            n += 1

    if is_new:
        provider = {
            "id": provider_id,
            "name": str(body.get("name") or "自定义").strip() or "自定义",
            "notes": str(body.get("notes") or "").strip(),
            "base_url": str(body.get("base_url") or "").strip().rstrip("/"),
            "api_key": "",
            "enabled": bool(body.get("enabled", True)),
            "models": [],
            "vision": bool(body.get("vision", False)),
            "preset": False,
        }
        if not provider["base_url"]:
            raise AppError("base_url 不能为空")
        data["providers"].append(provider)
    else:
        provider = _find(data, provider_id)

    for field in ("name", "notes"):
        if isinstance(body.get(field), str) and body[field].strip():
            provider[field] = body[field].strip()
    if isinstance(body.get("base_url"), str) and body["base_url"].strip():
        provider["base_url"] = body["base_url"].strip().rstrip("/")
    if isinstance(body.get("api_key"), str) and body["api_key"].strip():
        provider["api_key"] = body["api_key"].strip()
    if "enabled" in body:
        provider["enabled"] = bool(body.get("enabled"))
    if "vision" in body:
        provider["vision"] = bool(body.get("vision"))
    if isinstance(body.get("clear_key"), bool) and body["clear_key"]:
        provider["api_key"] = ""
    model = body.get("model")
    if isinstance(model, str) and model.strip() and model.strip() not in provider["models"]:
        provider["models"].append(model.strip())
    extra = body.get("models")
    if isinstance(extra, list):
        for one in extra:
            name = str(one).strip()
            if name and name not in provider["models"]:
                provider["models"].append(name)
    if isinstance(body.get("replace_models"), list):
        provider["models"] = [str(x).strip() for x in body["replace_models"] if str(x).strip()]

    save(settings, data)
    return public(settings)


def remove(settings: Settings, provider_id: str) -> dict[str, Any]:
    data = load(settings)
    provider = _find(data, provider_id)
    data["providers"].remove(provider)
    if data["active"].get("provider") == provider_id:
        data["active"] = {"provider": "", "model": ""}
    save(settings, data)
    return public(settings)


def toggle(settings: Settings, provider_id: str, enabled: bool) -> dict[str, Any]:
    data = load(settings)
    provider = _find(data, provider_id)
    provider["enabled"] = bool(enabled)
    save(settings, data)
    return public(settings)


def add_model(settings: Settings, provider_id: str, model_id: str) -> dict[str, Any]:
    data = load(settings)
    provider = _find(data, provider_id)
    model_id = str(model_id or "").strip()
    if not model_id:
        raise AppError("模型 ID 不能为空")
    if model_id not in provider["models"]:
        provider["models"].append(model_id)
        save(settings, data)
    return public(settings)


def remove_model(settings: Settings, provider_id: str, model_id: str) -> dict[str, Any]:
    data = load(settings)
    provider = _find(data, provider_id)
    provider["models"] = [m for m in provider["models"] if m != model_id]
    if data["active"].get("provider") == provider_id and data["active"].get("model") == model_id:
        data["active"]["model"] = provider["models"][0] if provider["models"] else ""
    save(settings, data)
    return public(settings)


def resolve(settings: Settings, provider_id: str, model: str = "") -> dict[str, str]:
    data = load(settings)
    provider = _find(data, provider_id)
    model = (model or "").strip() or (provider["models"][0] if provider["models"] else "")
    if not model:
        raise AppError("这家提供商还没有模型，先加一个")
    if model not in provider["models"]:
        raise AppError(f"{provider['name']} 里没有模型 {model}")
    return {
        "id": provider["id"],
        "name": provider["name"],
        "base_url": provider["base_url"],
        "api_key": provider.get("api_key", ""),
        "model": model,
    }


def activate(settings: Settings, provider_id: str, model: str = "") -> dict[str, str]:
    resolved = resolve(settings, provider_id, model)
    data = load(settings)
    data["active"] = {"provider": resolved["id"], "model": resolved["model"]}
    save(settings, data)
    return resolved


# ---------------- 目录 / 拉模型 / 清空壳 ----------------
def catalog() -> list[dict[str, Any]]:
    """「添加提供商」的可选模板（不含 Key，不含假模型）。"""
    return llm_catalog()


def add_from_preset(settings: Settings, preset_id: str, api_key: str = "", base_url: str = "",
                    name: str = "", model: str = "") -> dict[str, Any]:
    preset = llm_preset(str(preset_id or "").strip())
    if preset is None:
        raise AppError(f"目录里没有这个预置：{preset_id}")
    data = load(settings)
    pid = preset["id"]
    n = 2
    while any(p["id"] == pid for p in data["providers"]):
        pid = f"{preset['id']}-{n}"
        n += 1
    provider = {
        "id": pid,
        "name": (name or preset["name"]).strip(),
        "notes": preset.get("notes", ""),
        "base_url": (base_url or preset["base_url"]).strip().rstrip("/"),
        "api_key": str(api_key or "").strip(),
        "enabled": True,
        "models": [model.strip()] if model and model.strip() else [],
        "vision": False,
        "preset": True,
    }
    if not provider["base_url"]:
        raise AppError("这家预置没有默认 BASE URL，请手填一个")
    data["providers"].append(provider)
    save(settings, data)
    view = public(settings)
    view["added"] = provider["id"]
    return view


def fetch_models(settings: Settings, provider_id: str) -> dict[str, Any]:
    """调服务商的 GET /models，把真实模型列表写回 store。"""
    data = load(settings)
    provider = _find(data, provider_id)
    key = str(provider.get("api_key") or "")
    if not key and not any(item.get("keyless") and item["id"] == provider["id"] for item in catalog()):
        raise AppError(f"{provider['name']} 还没填 Key，先保存 Key 再拉模型")
    url = provider["base_url"].rstrip("/") + "/models"
    try:
        body = get_json(url, bearer(key), timeout=30)
    except NetError as exc:
        raise AppError(f"{provider['name']} 拉取模型失败：{exc}") from exc

    raw_items = body.get("data")
    if not isinstance(raw_items, list):
        raw_items = body.get("models")
    if not isinstance(raw_items, list):
        raw_items = body.get("_list")
    models: list[str] = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("name") or item.get("model")
        else:
            mid = item
        if isinstance(mid, str) and mid.strip() and mid.strip() not in models:
            models.append(mid.strip())
    if not models:
        raise AppError(f"{provider['name']} 没返回可用模型（{url}）：{str(body)[:200]}")

    provider["models"] = models
    active = data.get("active") or {}
    if active.get("provider") == provider["id"] and active.get("model") not in models:
        data["active"] = {"provider": provider["id"], "model": models[0]}
    save(settings, data)
    return {"provider": provider["id"], "models": models, "count": len(models), "url": url}


def prune_empty(settings: Settings) -> dict[str, Any]:
    """删掉「预置模板但没 Key、也不是当前在用」的空壳，用户可以随时再添加。"""
    data = load(settings)
    active = (data.get("active") or {}).get("provider", "")
    keep, removed = [], []
    for provider in data["providers"]:
        empty = not provider.get("api_key")
        if empty and provider["id"] != active and provider.get("preset"):
            removed.append(provider["name"])
            continue
        keep.append(provider)
    if removed:
        data["providers"] = keep
        save(settings, data)
    return {"removed": removed, "count": len(removed), **public(settings)}


def set_vision(settings: Settings, provider_id: str, vision: bool) -> dict[str, Any]:
    data = load(settings)
    provider = _find(data, provider_id)
    provider["vision"] = bool(vision)
    save(settings, data)
    return public(settings)


def active_meta(settings: Settings) -> dict[str, Any]:
    """当前在用的提供商信息（给 Agent 决定要不要发图片块）。"""
    data = load(settings)
    active = data.get("active") or {}
    for provider in data["providers"]:
        if provider["id"] == active.get("provider"):
            return {
                "id": provider["id"],
                "name": provider["name"],
                "base_url": provider["base_url"],
                "model": active.get("model", ""),
                "vision": bool(provider.get("vision")),
            }
    return {"id": "", "name": "", "base_url": settings.llm_base_url, "model": settings.llm_model, "vision": False}
