"""Cookie 工具：读写 Cookie、从 storage_state / 浏览器 CDP 导入。"""

from __future__ import annotations

import json
from typing import Any, Callable

from ..errors import ToolError
from ..security import display_path, resolve_in_sandbox
from .base import ToolContext, ToolRegistry

StoreFactory = Callable[[], Any]


def register(registry: ToolRegistry, get_store: StoreFactory) -> None:
    def get_cookie(ctx: ToolContext, args: dict[str, Any]) -> str:
        domain = str(args.get("domain") or "").strip() or None
        return json.dumps(get_store().get(domain), ensure_ascii=False, indent=2)

    def set_cookie(ctx: ToolContext, args: dict[str, Any]) -> str:
        domain = str(args.get("domain") or "").strip()
        if not domain:
            raise ToolError("domain 不能为空，例如 example.com")
        raw = args.get("cookie")
        if raw is None:
            raise ToolError("cookie 不能为空（JSON 数组 / JSON 对象 / 'a=1; b=2' 字符串）")
        summary = get_store().set(domain, raw, merge=bool(args.get("merge", True)))
        return f"OK: {json.dumps(summary, ensure_ascii=False)}"

    def delete_cookie(ctx: ToolContext, args: dict[str, Any]) -> str:
        domain = str(args.get("domain") or "").strip()
        if not domain:
            raise ToolError("domain 不能为空")
        return f"OK: {json.dumps(get_store().delete(domain), ensure_ascii=False)}"

    def cookie_header(ctx: ToolContext, args: dict[str, Any]) -> str:
        domain = str(args.get("domain") or "").strip()
        if not domain:
            raise ToolError("domain 不能为空")
        header = get_store().to_header(domain)
        if not header:
            raise ToolError(f"{domain} 没有可用 Cookie")
        return f"Cookie: {header}"

    def import_storage_state(ctx: ToolContext, args: dict[str, Any]) -> str:
        path = resolve_in_sandbox(str(args.get("path") or ""))
        domain = str(args.get("domain") or "").strip() or None
        summary = get_store().import_storage_state(path, domain)
        return f"OK: 已从 {display_path(path)} 导入\n{json.dumps(summary, ensure_ascii=False, indent=2)}"

    def import_from_browser(ctx: ToolContext, args: dict[str, Any]) -> str:
        cdp_url = str(args.get("cdp_url") or "http://127.0.0.1:9222").strip()
        domain = str(args.get("domain") or "").strip() or None
        summary = get_store().import_from_cdp(cdp_url, domain)
        return f"OK: 已从浏览器导入\n{json.dumps(summary, ensure_ascii=False, indent=2)}"

    registry.register(
        "get_cookie",
        "读取已保存的 Cookie（按域名模糊匹配）。不传 domain 则返回全部。",
        {"type": "object", "properties": {"domain": {"type": "string", "description": "如 example.com，可省略"}}},
        get_cookie,
    )
    registry.register(
        "set_cookie",
        "保存 Cookie 到本地仓库。cookie 支持 JSON 数组、JSON 对象或 'a=1; b=2' 字符串。",
        {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "cookie": {
                    "description": "Cookie 内容：JSON 数组 / JSON 对象 / 'a=1; b=2' 字符串",
                    "anyOf": [{"type": "string"}, {"type": "array"}, {"type": "object"}],
                },
                "merge": {"type": "boolean", "default": True, "description": "同名 Cookie 是否合并覆盖"},
            },
            "required": ["domain", "cookie"],
        },
        set_cookie,
    )
    registry.register(
        "delete_cookie",
        "删除某个域名下已保存的 Cookie。",
        {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
        delete_cookie,
    )
    registry.register(
        "get_cookie_header",
        "把某个域名的 Cookie 拼成可直接放进请求头的 'name=value; name2=value2' 字符串。",
        {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
        cookie_header,
    )
    registry.register(
        "import_cookie_from_storage_state",
        "从 Playwright 的 storage_state.json（浏览器登录态导出文件）导入 Cookie。path 为沙箱内路径，domain 可选。",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "storage_state.json 在沙箱内的路径"}, "domain": {"type": "string"}},
            "required": ["path"],
        },
        import_storage_state,
    )
    registry.register(
        "import_cookie_from_browser",
        "通过 Playwright 连接已登录的 Chrome（CDP，默认 127.0.0.1:9222）直接读取 Cookie（需额外装 playwright）。",
        {
            "type": "object",
            "properties": {"cdp_url": {"type": "string", "default": "http://127.0.0.1:9222"}, "domain": {"type": "string"}},
        },
        import_from_browser,
    )
