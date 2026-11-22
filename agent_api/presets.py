"""预置目录：只当「可选模板」用，不再默认塞一堆假模型进你的列表。

用户在界面上点「添加提供商」→ 从目录挑一家（或选自定义）→ 填 Key →
点「拉取模型」从 /models 拿真实模型列表。没 Key 的模板不会出现在你的列表里。
"""

from __future__ import annotations

from typing import Any

# ---------------- 对话模型（OpenAI 兼容 /chat/completions + /models） ----------------
LLM_PRESETS: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "notes": "官方 OpenAI 兼容端点，chat / reasoner 都带思考",
        "base_url": "https://api.deepseek.com/v1",
        "model_hint": "deepseek-chat",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    {
        "id": "siliconflow",
        "name": "硅基流动",
        "notes": "国内聚合，模型名带 厂商/ 前缀；/models 能列出全部",
        "base_url": "https://api.siliconflow.cn/v1",
        "model_hint": "deepseek-ai/DeepSeek-V3",
        "key_url": "https://cloud.siliconflow.cn/account/ak",
    },
    {
        "id": "sensenova",
        "name": "商汤日日新",
        "notes": "Token Plan，OpenAI 兼容",
        "base_url": "https://token.sensenova.cn/v1",
        "model_hint": "sensenova-u1.5-lite",
    },
    {
        "id": "sensenova-dz",
        "name": "商汤大装置",
        "notes": "compatible-mode 端点",
        "base_url": "https://api.sensenova.cn/compatible-mode/v1",
        "model_hint": "SenseChat-5",
    },
    {
        "id": "moonshot",
        "name": "月之暗面",
        "notes": "Kimi 系列",
        "base_url": "https://api.moonshot.cn/v1",
        "model_hint": "moonshot-v1-8k",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
    },
    {
        "id": "dashscope",
        "name": "通义千问",
        "notes": "阿里云百炼 compatible-mode",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_hint": "qwen-plus",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "notes": "一个 Key 转 200+ 模型，/models 很长",
        "base_url": "https://openrouter.ai/api/v1",
        "model_hint": "openai/gpt-4o-mini",
        "key_url": "https://openrouter.ai/keys",
    },
    {
        "id": "aihubmix",
        "name": "AiHubMix",
        "notes": "gpt / claude / gemini 聚合",
        "base_url": "https://aihubmix.com/v1",
        "model_hint": "gpt-4o-mini",
    },
    {
        "id": "gemini",
        "name": "Gemini",
        "notes": "Google 的 OpenAI 兼容端点",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_hint": "gemini-2.0-flash",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "notes": "官方端点（国内需代理）",
        "base_url": "https://api.openai.com/v1",
        "model_hint": "gpt-4o-mini",
    },
    {
        "id": "ollama",
        "name": "本地 Ollama",
        "notes": "本机 http://127.0.0.1:11434，不用 Key",
        "base_url": "http://127.0.0.1:11434/v1",
        "model_hint": "qwen2.5",
        "keyless": True,
    },
]

# ---------------- 图片生成 ----------------
IMAGE_PRESETS: list[dict[str, Any]] = [
    {
        "id": "siliconflow-img",
        "name": "硅基流动 · Kolors",
        "notes": "OpenAI 形状的 /images/generations，按张计费",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Kwai-Kolors/Kolors",
        "mode": "openai",
    },
    {
        "id": "openai-img",
        "name": "OpenAI · gpt-image",
        "notes": "官方 /images/generations，需要代理",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-image-1",
        "mode": "openai",
    },
    {
        "id": "aihubmix-img",
        "name": "AiHubMix · 图像",
        "notes": "聚合端的 /images/generations",
        "base_url": "https://aihubmix.com/v1",
        "model": "gpt-image-1",
        "mode": "openai",
    },
    {
        "id": "custom-img",
        "name": "自定义（OpenAI 形状）",
        "notes": "中转站 /images/generations，字段可改",
        "base_url": "",
        "model": "",
        "mode": "openai",
    },
]

# ---------------- 视频生成 ----------------
VIDEO_PRESETS: list[dict[str, Any]] = [
    {
        "id": "openai-video",
        "name": "OpenAI · Sora",
        "notes": "POST /videos → 轮询 /videos/{id} → GET /videos/{id}/content",
        "base_url": "https://api.openai.com/v1",
        "model": "sora-2",
        "mode": "openai_video",
    },
    {
        "id": "siliconflow-video",
        "name": "硅基流动 · 万相",
        "notes": "POST /video/submit → POST /video/status 轮询",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Wan-AI/Wan2.1-T2V-14B",
        "mode": "siliconflow_video",
    },
    {
        "id": "custom-video",
        "name": "自定义（异步任务）",
        "notes": "中转站 POST /videos/generations，返回 task_id 或 url，按约定轮询",
        "base_url": "",
        "model": "",
        "mode": "generic_video",
    },
]

# ---------------- 搜索 ----------------
SEARCH_PRESETS: list[dict[str, Any]] = [
    {
        "id": "tavily",
        "name": "Tavily",
        "notes": "为 Agent 做的搜索 API，1000 次/月免费额度",
        "endpoint": "https://api.tavily.com/search",
        "key_url": "https://app.tavily.com/home",
    },
    {
        "id": "bocha",
        "name": "博查 Bocha",
        "notes": "国内网页搜索，返回摘要",
        "endpoint": "https://api.bochaai.com/v1/web-search",
        "key_url": "https://open.bochaai.com",
    },
    {
        "id": "serper",
        "name": "Serper (Google)",
        "notes": "Google 结果，2500 次免费",
        "endpoint": "https://google.serper.dev/search",
        "key_url": "https://serper.dev/api-key",
    },
    {
        "id": "ddg",
        "name": "DuckDuckGo（免 Key）",
        "notes": "抓 HTML 结果页，不用 Key；偶尔被限流",
        "endpoint": "https://html.duckduckgo.com/html/",
    },
    {
        "id": "custom",
        "name": "自定义 JSON 搜索",
        "notes": "你自己的搜索网关，返回 {results:[{title,url,snippet}]}",
        "endpoint": "",
    },
]


def llm_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "notes": item.get("notes", ""),
            "base_url": item["base_url"],
            "model_hint": item.get("model_hint", ""),
            "keyless": bool(item.get("keyless")),
        }
        for item in LLM_PRESETS
    ]


def llm_preset(preset_id: str) -> dict[str, Any] | None:
    for item in LLM_PRESETS:
        if item["id"] == preset_id:
            return dict(item)
    return None


def preset_ids() -> set[str]:
    return {item["id"] for item in LLM_PRESETS}


def media_presets(kind: str) -> list[dict[str, Any]]:
    return [dict(item) for item in (IMAGE_PRESETS if kind == "image" else VIDEO_PRESETS)]


def media_preset(kind: str, preset_id: str) -> dict[str, Any] | None:
    for item in (IMAGE_PRESETS if kind == "image" else VIDEO_PRESETS):
        if item["id"] == preset_id:
            return dict(item)
    return None


def search_presets() -> list[dict[str, Any]]:
    return [dict(item) for item in SEARCH_PRESETS]


def search_preset(preset_id: str) -> dict[str, Any] | None:
    for item in SEARCH_PRESETS:
        if item["id"] == preset_id:
            return dict(item)
    return None