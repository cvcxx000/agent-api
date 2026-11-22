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

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


DEFAULT_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "sensenova",
        "name": "商汤日日新",
        "notes": "Token Plan，OpenAI 兼容",
        "base_url": "https://token.sensenova.cn/v1",
        "enabled": True,
        "models": ["sensenova-u1.5-lite"],
    },
    {
        "id": "sensenova-dz",
        "name": "商汤大装置",
        "notes": "compatible-mode",
        "base_url": "https://api.sensenova.cn/compatible-mode/v1",
        "enabled": True,
        "models": ["SenseChat-5"],
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "notes": "deepseek-chat / deepseek-reasoner",
        "base_url": "https://api.deepseek.com/v1",
        "enabled": True,
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    {
        "id": "siliconflow",
        "name": "硅基流动",
        "notes": "国内聚合，模型按 厂商/模型 命名",
        "base_url": "https://api.siliconflow.cn/v1",
        "enabled": True,
        "models": ["deepseek-ai/DeepSeek-V3"],
    },
    {
        "id": "moonshot",
        "name": "月之暗面",
        "notes": "Kimi 系列",
        "base_url": "https://api.moonshot.cn/v1",
        "enabled": True,
        "models": ["moonshot-v1-8k"],
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "notes": "一个 Key 转 200+ 模型",
        "base_url": "https://openrouter.ai/api/v1",
        "enabled": True,
        "models": ["openai/gpt-4o-mini"],
    },
    {
        "id": "gemini",
        "name": "Gemini",
        "notes": "Google 的 OpenAI 兼容端点",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "enabled": False,
        "models": ["gemini-2.0-flash"],
    },
    {
        "id": "aihubmix",
        "name": "AiHubMix",
        "notes": "支持 gpt / claude / gemini 等 200+ 模型",
        "base_url": "https://aihubmix.com/v1",
        "enabled": False,
        "models": ["gpt-4o-mini"],
    },
    {
        "id": "dashscope",
        "name": "通义千问",
        "notes": "阿里云百炼 compatible-mode",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "enabled": False,
        "models": ["qwen-plus", "qwen-max"],
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "notes": "官方端点（国内需代理）",
        "base_url": "https://api.openai.com/v1",
        "enabled": False,
        "models": ["gpt-4o-mini"],
    },
    {
        "id": "ollama",
        "name": "本地 Ollama",
        "notes": "本机 http://127.0.0.1:11434",
        "base_url": "http://127.0.0.1:11434/v1",
        "enabled": False,
        "models": ["qwen2.5"],
    },
]


def _store_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "providers.json"


def _slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")
    return out or "custom"


def _tail(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return ""
    return key[-4:] if len(key) > 8 else "…"


def _seed(settings: Settings) -> dict[str, Any]:
    providers = []
    for item in DEFAULT_PROVIDERS:
        provider = dict(item)
        provider["api_key"] = ""
        provider["models"] = list(item["models"])
        providers.append(provider)

    base = (settings.llm_base_url or "").rstrip("/")
    model = settings.llm_model or ""
    active_id = ""
    matched_model = ""
    for provider in providers:
        if base and provider["base_url"].rstrip("/") == base:
            active_id = provider["id"]
            matched_model = model if model in provider["models"] else (provider["models"][0] if provider["models"] else "")
            if base and (settings.llm_api_key or ""):
                provider["api_key"] = settings.llm_api_key
            if model and model not in provider["models"]:
                provider["models"].insert(0, model)
                matched_model = model
            provider["enabled"] = True
            break
    if not active_id:
        active_id = providers[0]["id"]
        matched_model = providers[0]["models"][0] if providers[0]["models"] else ""
    return {"active": {"provider": active_id, "model": matched_model}, "providers": providers}


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
            for provider in data["providers"]:
                provider.setdefault("api_key", "")
                provider.setdefault("enabled", False)
                provider.setdefault("models", [])
                provider.setdefault("notes", "")
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
    if isinstance(body.get("clear_key"), bool) and body["clear_key"]:
        provider["api_key"] = ""
    model = body.get("model")
    if isinstance(model, str) and model.strip() and model.strip() not in provider["models"]:
        provider["models"].append(model.strip())

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
