"""联网搜索：配置存 data/search.json，支持 Tavily / 博查 / Serper / DuckDuckGo（免 Key）/ 自定义。

只用 urllib，不装库。没配 Key 时也能用 DuckDuckGo 的 HTML 结果页凑一段，
但会被限流，正式用还是建议填一家。
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import AppError
from .net import NetError, bearer, get_json, post_json
from .presets import search_preset, search_presets

_DEFAULT: dict[str, Any] = {
    "provider": "",
    "name": "",
    "endpoint": "",
    "api_key": "",
    "max_results": 5,
}

_TAG_RE = re.compile(r"<[^>]+>")
_A_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I
)
_SNIP_RE = re.compile(r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S | re.I)


def _path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "search.json"


def load(settings: Settings) -> dict[str, Any]:
    path = _path(settings)
    cfg = dict(_DEFAULT)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update({k: v for k, v in raw.items() if v is not None})
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save(settings: Settings, cfg: dict[str, Any]) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def public(settings: Settings) -> dict[str, Any]:
    cfg = load(settings)
    key = str(cfg.get("api_key") or "")
    return {
        "provider": cfg.get("provider", ""),
        "name": cfg.get("name", ""),
        "endpoint": cfg.get("endpoint", ""),
        "max_results": cfg.get("max_results", 5),
        "has_key": bool(key),
        "key_tail": key[-4:] if len(key) > 8 else ("…" if key else ""),
        "ready": bool(cfg.get("provider")),
        "presets": search_presets(),
    }


def configure(settings: Settings, body: dict[str, Any]) -> dict[str, Any]:
    cfg = load(settings)
    preset_id = str(body.get("provider") or body.get("preset") or "").strip()
    if preset_id:
        preset = search_preset(preset_id)
        if preset is None:
            raise AppError(f"没有这个搜索预置：{preset_id}")
        cfg["provider"] = preset["id"]
        cfg["name"] = preset["name"]
        cfg["endpoint"] = preset.get("endpoint") or cfg.get("endpoint", "")
    endpoint = body.get("endpoint")
    if isinstance(endpoint, str) and endpoint.strip():
        cfg["endpoint"] = endpoint.strip()
    key = body.get("api_key")
    if isinstance(key, str) and key.strip():
        cfg["api_key"] = key.strip()
    if body.get("clear_key") is True:
        cfg["api_key"] = ""
    count = body.get("max_results")
    if count is not None and str(count).strip():
        try:
            cfg["max_results"] = max(1, min(20, int(float(count))))
        except ValueError:
            raise AppError("max_results 必须是数字") from None
    if not cfg.get("provider"):
        raise AppError("先选一家搜索服务")
    save(settings, cfg)
    return public(settings)


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _ddg_link(href: str) -> str:
    href = html.unescape(href)
    if "duckduckgo.com/l/" in href:
        query = urllib.parse.urlparse(href).query
        params = urllib.parse.parse_qs(query)
        if params.get("uddg"):
            return params["uddg"][0]
    return href


def _ddg(settings: Settings, query: str, count: int, cfg: dict[str, Any]) -> dict[str, Any]:
    endpoint = cfg.get("endpoint") or "https://html.duckduckgo.com/html/"
    url = endpoint + "?" + urllib.parse.urlencode({"q": query})
    from .net import get_bytes

    try:
        raw, _ctype = get_bytes(url, {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36"}, timeout=30)
    except NetError as exc:
        raise AppError(f"DuckDuckGo 抓不到：{exc}") from exc
    text = raw.decode("utf-8", "replace")
    links = _A_RE.findall(text)
    snippets = [_clean(s) for s in _SNIP_RE.findall(text)]
    results = []
    for i, (href, title) in enumerate(links[:count]):
        results.append(
            {
                "title": _clean(title),
                "url": _ddg_link(href),
                "snippet": snippets[i] if i < len(snippets) else "",
            }
        )
    if not results:
        raise AppError("DuckDuckGo 没有返回结果（可能被限流，换一家或稍后再试）")
    return {"provider": "ddg", "query": query, "results": results, "answer": ""}


def run(settings: Settings, query: str, count: int | None = None) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        raise AppError("query 不能为空")
    cfg = load(settings)
    provider = str(cfg.get("provider") or "")
    if not provider:
        raise AppError("还没配置搜索：设置 → 搜索服务，选一家（DuckDuckGo 免 Key）")
    limit = max(1, min(20, int(count or cfg.get("max_results") or 5)))
    key = str(cfg.get("api_key") or "")

    if provider == "tavily":
        if not key:
            raise AppError("Tavily 需要 API Key")
        try:
            data = post_json(
                cfg["endpoint"] or "https://api.tavily.com/search",
                {"api_key": key, "query": query, "max_results": limit, "include_answer": True},
                timeout=60,
            )
        except NetError as exc:
            raise AppError(f"Tavily 搜索失败：{exc}") from exc
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:600]}
            for r in (data.get("results") or [])
            if isinstance(r, dict)
        ]
        return {"provider": "tavily", "query": query, "results": results, "answer": data.get("answer") or ""}

    if provider == "bocha":
        if not key:
            raise AppError("博查需要 API Key")
        try:
            data = post_json(
                cfg["endpoint"] or "https://api.bochaai.com/v1/web-search",
                {"query": query, "summary": True, "count": limit, "freshness": "noLimit"},
                bearer(key),
                timeout=60,
            )
        except NetError as exc:
            raise AppError(f"博查搜索失败：{exc}") from exc
        pages = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
        results = [
            {"title": p.get("name", ""), "url": p.get("url", ""), "snippet": (p.get("snippet") or p.get("summary") or "")[:600]}
            for p in pages
            if isinstance(p, dict)
        ]
        return {"provider": "bocha", "query": query, "results": results, "answer": ""}

    if provider == "serper":
        if not key:
            raise AppError("Serper 需要 API Key")
        try:
            data = post_json(
                cfg["endpoint"] or "https://google.serper.dev/search",
                {"q": query, "num": limit},
                {"X-API-KEY": key},
                timeout=60,
            )
        except NetError as exc:
            raise AppError(f"Serper 搜索失败：{exc}") from exc
        results = [
            {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
            for r in (data.get("organic") or [])
            if isinstance(r, dict)
        ]
        return {"provider": "serper", "query": query, "results": results, "answer": data.get("answerBox", {}).get("snippet", "") if isinstance(data.get("answerBox"), dict) else ""}

    if provider == "ddg":
        return _ddg(settings, query, limit, cfg)

    # 自定义：GET/POST 都试，字段按常见形状认
    endpoint = cfg.get("endpoint") or ""
    if not endpoint:
        raise AppError("自定义搜索要先填 endpoint")
    url = endpoint.replace("{q}", urllib.parse.quote(query)).replace("{query}", urllib.parse.quote(query))
    headers = bearer(key)
    try:
        if "{q}" in endpoint or "{query}" in endpoint:
            data = get_json(url, headers, timeout=45)
        else:
            data = post_json(url, {"query": query, "q": query, "count": limit, "key": key}, headers, timeout=45)
    except NetError as exc:
        raise AppError(f"自定义搜索失败：{exc}") from exc
    items = data.get("results") or data.get("data") or data.get("organic") or []
    results = []
    for r in items if isinstance(items, list) else []:
        if isinstance(r, str):
            results.append({"title": r, "url": r, "snippet": ""})
        elif isinstance(r, dict):
            results.append(
                {
                    "title": r.get("title") or r.get("name") or "",
                    "url": r.get("url") or r.get("link") or "",
                    "snippet": (r.get("snippet") or r.get("content") or r.get("summary") or "")[:600],
                }
            )
    return {"provider": "custom", "query": query, "results": results[:limit], "answer": data.get("answer") or ""}
