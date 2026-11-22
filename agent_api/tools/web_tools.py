"""联网工具：web_search（走配置好的搜索服务）+ fetch_url（抓网页正文）。

零依赖：搜索在 agent_api/search.py，抓页面用 urllib + 正则去标签。
"""

from __future__ import annotations

import html
import re
from typing import Any

from ..config import settings
from ..errors import AppError
from ..net import NetError, get_bytes
from ..search import run as run_search
from .base import ToolContext, ToolRegistry

_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\n{3,}")


def _page_text(raw: str) -> str:
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub("\n", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return _SPACE_RE.sub("\n\n", text)


def register(registry: ToolRegistry) -> None:
    def web_search(ctx: ToolContext, args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            raise AppError("query 不能为空")
        count = args.get("count")
        result = run_search(settings, query, int(count) if count not in (None, "") else None)
        rows = result.get("results") or []
        if not rows:
            return f"搜索「{query}」没有结果（服务：{result.get('provider')}）。"
        out = [f"搜索「{query}」· {result.get('provider')} · {len(rows)} 条"]
        if result.get("answer"):
            out.append(f"摘要：{result['answer']}")
        for i, item in enumerate(rows, 1):
            out.append(f"{i}. {item.get('title') or '(无标题)'}\n   {item.get('url') or ''}\n   {item.get('snippet') or ''}")
        return "\n".join(out)

    def fetch_url(ctx: ToolContext, args: dict[str, Any]) -> str:
        url = str(args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise AppError("url 必须是 http(s) 开头")
        limit = int(args.get("max_chars") or 8000)
        try:
            raw, ctype = get_bytes(url, {"User-Agent": "Mozilla/5.0 (Linux; Android 13)"}, timeout=45)
        except NetError as exc:
            raise AppError(f"抓取失败：{exc}") from exc
        if "image" in ctype or "video" in ctype:
            raise AppError(f"这是 {ctype} 资源，不是网页；用下载工具或直接看地址")
        text = _page_text(raw.decode("utf-8", "replace"))
        if len(text) > limit:
            text = text[:limit] + f"\n…（截断，原长 {len(text)} 字）"
        return f"{url}\n\n{text}"

    registry.register(
        "web_search",
        "联网搜索（Tavily / 博查 / Serper / DuckDuckGo）。返回标题、链接和摘要；"
        "要读全文再对具体链接调用 fetch_url。",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索词"},
                "count": {"type": "integer", "description": "要几条，1-20，默认用配置里的"},
            },
            "required": ["query"],
        },
        web_search,
    )
    registry.register(
        "fetch_url",
        "抓取网页并转成纯文本（去脚本和标签）。适合读文档、文章、接口说明。",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http(s) 地址"},
                "max_chars": {"type": "integer", "description": "最多返回多少字，默认 8000"},
            },
            "required": ["url"],
        },
        fetch_url,
    )
