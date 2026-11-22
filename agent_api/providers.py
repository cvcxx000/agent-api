"""提供商（provider）配置：Key、端点、模型列表都落在这里。

设计：不做预设假模型。提供商只能是：
1. 用户自己从预设列表挑 + 填 Key + 拉真实模型列表；
2. 用户完全自定义（名字 / 端点 / Key / 模型全手填）。
`data/providers.json` 里只存用户真实配过的东西，不给任何占位模型。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Any

from .errors import AppError
from .utils import new_id

_PRESETS: list[dict[str, Any]] = [
    {
        "slug": "openai",
        "name": "OpenAI",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "hint": "sk-…",
        "models_endpoint": "https://api.openai.com/v1/models",
        "docs": "https://platform.openai.com/api-keys",
    },
    {
        "slug": "deepseek",
        "name": "DeepSeek",
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "hint": "sk-…",
        "models_endpoint": "https://api.deepseek.com/v1/models",
        "docs": "https://platform.deepseek.com/api_keys",
    },
    {
        "slug": "moonshot",
        "name": "Moonshot / Kimi",
        "endpoint": "https://api.moonshot.cn/v1/chat/completions",
        "hint": "sk-…",
        "models_endpoint": "https://api.moonshot.cn/v1/models",
        "docs": "https://platform.moonshot.cn/console/api-keys",
    },
    {
        "slug": "dashscope",
        "name": "阿里百炼（通义）",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "hint": "sk-…",
        "models_endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
        "docs": "https://bailian.console.aliyun.com/?apiKey=1",
    },
    {
        "slug": "zhipu",
        "name": "智谱 GLM",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "hint": "…",
        "models_endpoint": "https://open.bigmodel.cn/api/paas/v4/models",
        "docs": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    {
        "slug": "siliconflow",
        "name": "硅基流动 SiliconFlow",
        "endpoint": "https://api.siliconflow.cn/v1/chat/completions",
        "hint": "sk-…",
        "models_endpoint": "https://api.siliconflow.cn/v1/models",
        "docs": "https://cloud.siliconflow.cn/account/ak",
    },
    {
        "slug": "google",
        "name": "Google Gemini（OpenAI 兼容）",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "hint": "AIza…",
        "models_endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/models",
        "docs": "https://aistudio.google.com/app/apikey",
    },
    {
        "slug": "openrouter",
        "name": "OpenRouter",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "hint": "sk-or-v1-…",
        "models_endpoint": "https://openrouter.ai/api/v1/models",
        "docs": "https://openrouter.ai/keys",
    },
    {
        "slug": "together",
        "name": "Together AI",
        "endpoint": "https://api.together.xyz/v1/chat/completions",
        "hint": "…",
        "models_endpoint": "https://api.together.xyz/v1/models",
        "docs": "https://api.together.ai/settings/api-keys",
    },
    {
        "slug": "local",
        "name": "本地 / 自建（Ollama、vLLM、OneAPI）",
        "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
        "hint": "本地服务可以留空",
        "models_endpoint": "http://127.0.0.1:11434/v1/models",
        "docs": "https://github.com/ollama/ollama/blob/main/docs/openai.md",
    },
]

# 参数名带这些字样的，一般都是“能吃图”的多模态模型。
_VISION_RE = re.compile(
    r"(vision|-vl\b|/vl|4o\b|4\.1|gemini|claude-3|glm-4v|omni|multimodal|pixtral|llava|qwen2[.-]?5?-vl|moonshot|kimi(?!-k2))",
    re.I,
)

# 只有这些**明确**是纯文本模型：没有图像输入能力，发图会被 400 拒掉。
_TEXT_ONLY_RE = re.compile(
    r"^\s*(deepseek-(chat|reasoner|coder|v3)|gpt-3\.5|o3-mini|text-davinci|claude-2|qwen-max|chatglm-?[23](?!-)"
    r"|ernie-(speed|tiny)|command-r|yi-(large|medium))\b",
    re.I,
)


def looks_vision(model: str) -> bool:
    """默认当「能吃图」，只有认得出是纯文本模型才关。

    以前是反过来的：认不出就当不支持，结果一堆能看图的模型被静音。
    宁可发一次图让服务端报错（会被 agent 降级重试），也别把用户的图悄悄丢掉。
    """
    name = str(model or "").strip()
    if not name:
        return True
    if _VISION_RE.search(name):
        return True
    if _TEXT_ONLY_RE.search(name):
        return False
    return True


def vision_enabled(provider: dict[str, Any], model: str) -> bool:
    """三态：vision_off = 显式关；vision = 显式开；都没有 = 按模型名猜（默认开）。"""
    if provider.get("vision_off"):
        return False
    if provider.get("vision"):
        return True
    return looks_vision(model)


class ProviderStore:
    def __init__(self, path):
        self.path = path
        self._cache: dict[str, Any] | None = None

    # ---------- 读写 ----------

    def load(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        data: dict[str, Any] = {"active": None, "items": {}}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8")) or {}
                items = raw.get("items")
                if isinstance(items, dict):
                    data["items"] = {str(k): v for k, v in items.items() if isinstance(v, dict)}
                active = raw.get("active")
                if isinstance(active, str) and active in data["items"]:
                    data["active"] = active
            except (json.JSONDecodeError, OSError):
                data = {"active": None, "items": {}}
        self._cache = data
        return data

    def save(self) -> None:
        data = self.load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _items(self) -> dict[str, dict[str, Any]]:
        return self.load()["items"]

    # ---------- 对外视图 ----------

    @staticmethod
    def _tail(key: str) -> str:
        if not key:
            return ""
        if len(key) <= 6:
            return "**" + key[-2:]
        return key[-4:]

    def public(self, pid: str, item: dict[str, Any]) -> dict[str, Any]:
        models = [m for m in item.get("models") or [] if isinstance(m, str)]
        active = self.load()["active"]
        model = ""
        if active == pid and isinstance(item.get("model"), str):
            model = item["model"]
        return {
            "id": pid,
            "name": item.get("name") or pid,
            "preset": item.get("preset") or "",
            "endpoint": item.get("endpoint") or "",
            "key_tail": self._tail(item.get("key") or ""),
            "has_key": bool(item.get("key")),
            "models": models,
            "model": model,
            "vision": vision_enabled(item, model),
            "vision_off": bool(item.get("vision_off")),
            "probe_model": item.get("probe_model") or "",
            "created_at": item.get("created_at") or "",
            "updated_at": item.get("updated_at") or "",
        }

    def list(self) -> list[dict[str, Any]]:
        items = self._items()
        out = [self.public(pid, item) for pid, item in items.items()]
        out.sort(key=lambda x: x.get("created_at") or "")
        return out

    def get(self, pid: str) -> dict[str, Any]:
        item = self._items().get(pid)
        if not item:
            raise AppError("提供商不存在", 404)
        return item

    def active_id(self) -> str | None:
        return self.load()["active"]

    def active(self) -> dict[str, Any] | None:
        pid = self.active_id()
        if not pid:
            return None
        item = self._items().get(pid)
        if not item:
            return None
        model = item.get("model") or (item.get("models") or [""])[0]
        if not model:
            return None
        return {
            "id": pid,
            "name": item.get("name") or pid,
            "endpoint": item.get("endpoint") or "",
            "api_key": item.get("key") or "",
            "model": model,
            "vision": vision_enabled(item, model),
        }

    def snapshot(self) -> dict[str, Any]:
        active = self.active()
        return {
            "active": (active or {}).get("id"),
            "model": (active or {}).get("model", ""),
            "provider_name": (active or {}).get("name", ""),
            "vision": bool((active or {}).get("vision")),
            "configured": bool(active),
        }

    def presets(self) -> list[dict[str, Any]]:
        return [dict(p) for p in _PRESETS]

    # ---------- 写操作 ----------

    def upsert(self, pid: str | None, payload: dict[str, Any]) -> str:
        items = self._items()
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        payload = payload or {}
        pid = (pid or "").strip() or new_id("p")
        is_new = pid not in items
        if is_new:
            name = str(payload.get("name") or "").strip()
            if not name:
                raise AppError("给这家提供商起个名字")
            endpoint = str(payload.get("endpoint") or "").strip()
            if not endpoint:
                raise AppError("端点不能为空")
            items[pid] = {
                "name": name,
                "preset": str(payload.get("preset") or "custom"),
                "endpoint": endpoint,
                "key": str(payload.get("key") or "").strip(),
                "models": [],
                "model": "",
                "vision": None,
                "vision_off": None,
                "created_at": now,
            }
        item = items[pid]
        if "name" in payload and str(payload["name"]).strip():
            item["name"] = str(payload["name"]).strip()
        if "endpoint" in payload and str(payload["endpoint"]).strip():
            item["endpoint"] = str(payload["endpoint"]).strip()
        if "key" in payload:
            key = str(payload["key"] or "").strip()
            if key:
                item["key"] = key
        if "vision" in payload:
            flag = payload["vision"]
            item["vision"] = True if flag else None
            item["vision_off"] = None if flag else True
        item["updated_at"] = now
        self.save()
        return pid

    def remove(self, pid: str) -> None:
        items = self._items()
        if pid not in items:
            raise AppError("提供商不存在", 404)
        items.pop(pid)
        data = self.load()
        if data.get("active") == pid:
            data["active"] = None
        self.save()

    def toggle(self, pid: str, enabled: bool) -> None:
        items = self._items()
        item = items.get(pid)
        if not item:
            raise AppError("提供商不存在", 404)
        if enabled:
            model = item.get("model") or (item.get("models") or [""])[0]
            if not model:
                raise AppError("这家还没有模型，先拉取或选一个模型再启用")
            if not item.get("key") and "127.0.0.1" not in (item.get("endpoint") or "") and "localhost" not in (item.get("endpoint") or ""):
                raise AppError("这家还没填 API Key")
            self.load()["active"] = pid
        elif self.load().get("active") == pid:
            self.load()["active"] = None
        self.save()

    def add_model(self, pid: str, name: str, select: bool = True) -> None:
        item = self.get(pid)
        model = str(name or "").strip()
        if not model:
            raise AppError("模型名不能为空")
        models = [m for m in item.get("models") or [] if isinstance(m, str)]
        if model not in models:
            models.append(model)
            item["models"] = models
        if select or not item.get("model"):
            item["model"] = model
        item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()

    def remove_model(self, pid: str, name: str) -> None:
        item = self.get(pid)
        models = [m for m in item.get("models") or [] if isinstance(m, str) and m != name]
        item["models"] = models
        if item.get("model") == name:
            item["model"] = models[0] if models else ""
        item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()

    def set_model(self, pid: str, name: str) -> None:
        item = self.get(pid)
        model = str(name or "").strip()
        models = [m for m in item.get("models") or [] if isinstance(m, str)]
        if model not in models:
            models.append(model)
        item["models"] = models
        item["model"] = model
        item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()

    def set_probe_model(self, pid: str, name: str) -> None:
        item = self.get(pid)
        item["probe_model"] = str(name or "").strip()
        item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()

    def set_vision(self, pid: str, flag: Any) -> None:
        item = self.get(pid)
        if flag is None:
            item["vision"] = None
            item["vision_off"] = None
        elif flag:
            item["vision"] = True
            item["vision_off"] = None
        else:
            item["vision"] = None
            item["vision_off"] = True
        item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()

    def add_from_preset(self, slug: str, key: str = "") -> str:
        slug = (slug or "").strip()
        preset = next((p for p in _PRESETS if p["slug"] == slug), None)
        if not preset:
            raise AppError("没有这个预设", 404)
        for pid, item in self._items().items():
            if item.get("preset") == slug:
                if key and not item.get("key"):
                    item["key"] = key
                    item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    self.save()
                return pid
        pid = new_id("p")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._items()[pid] = {
            "name": preset["name"],
            "preset": slug,
            "endpoint": preset["endpoint"],
            "key": str(key or "").strip(),
            "models": [],
            "model": "",
            "vision": None,
            "vision_off": None,
            "created_at": now,
            "updated_at": now,
        }
        self.save()
        return pid

    def fetch_models(self, pid: str) -> list[str]:
        item = self.get(pid)
        key = item.get("key") or ""
        preset = next((p for p in _PRESETS if p["slug"] == item.get("preset")), None)
        url = (preset or {}).get("models_endpoint") or ""
        if not url:
            endpoint = item.get("endpoint") or ""
            url = endpoint.replace("/chat/completions", "/models")
        if not url:
            raise AppError("没有可用的模型列表地址")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001 - 交给上层展示
            raise AppError(f"拉取模型列表失败: {exc}", 502)
        models: list[str] = []
        if isinstance(raw, dict):
            data = raw.get("data")
            if isinstance(data, list):
                for m in data:
                    if isinstance(m, dict) and isinstance(m.get("id"), str):
                        models.append(m["id"])
            elif isinstance(raw.get("models"), list):
                for m in raw["models"]:
                    if isinstance(m, dict) and isinstance(m.get("name") or m.get("id"), str):
                        models.append(m.get("name") or m["id"])
        models = sorted({m.strip() for m in models if m and m.strip()})
        if not models:
            raise AppError("这家返回的模型列表是空的，可能 Key 不对或端点不支持 /models", 502)
        item["models"] = models
        if not item.get("model") or item["model"] not in models:
            item["model"] = models[0]
        item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()
        return models

    def meta(self, pid: str) -> dict[str, Any]:
        item = self._items().get(pid)
        if not item:
            return {"vision": "unknown"}
        return {"name": item.get("name") or pid, "vision": vision_enabled(item, item.get("model") or "")}
