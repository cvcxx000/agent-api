"""Cookie 仓库：把浏览器/手工拿到的 Cookie 存到 data/cookies.json，供 Agent 运行时取用。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .errors import ToolError
from .utils import now_iso

_KEEP_FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite", "url")


def _norm_domain(domain: str) -> str:
    return (domain or "").strip().lower().rstrip("/")


def _domain_match(key: str, target: str) -> bool:
    a, b = key.lstrip("."), target.lstrip(".")
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def parse_cookie_input(raw: Any) -> list[dict[str, Any]]:
    """支持：JSON 数组 / JSON 对象 / 'a=b; c=d' 字符串 / 单个 dict。"""
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, list):
        cookies: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                cookies.extend(parse_cookie_input(item))
            elif isinstance(item, dict):
                entry = {k: item[k] for k in _KEEP_FIELDS if k in item}
                if "name" not in entry or "value" not in entry:
                    raise ToolError("Cookie 对象至少需要 name 和 value")
                entry["name"] = str(entry["name"])
                entry["value"] = str(entry["value"])
                cookies.append(entry)
        return cookies
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text[:1] in "[{":
            try:
                return parse_cookie_input(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ToolError(f"Cookie JSON 解析失败：{exc}") from exc
        pairs = []
        for chunk in text.split(";"):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            name, _, value = chunk.partition("=")
            pairs.append({"name": name.strip(), "value": value.strip()})
        if not pairs:
            raise ToolError("未能从字符串中解析出 Cookie（示例：a=1; b=2）")
        return pairs
    raise ToolError("不支持的 Cookie 格式")


class CookieStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"updated_at": "", "domains": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"updated_at": "", "domains": {}}
        data.setdefault("domains", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        data["updated_at"] = now_iso()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def set(self, domain: str, raw_cookies: Any, merge: bool = True) -> dict[str, Any]:
        key = _norm_domain(domain)
        if not key:
            raise ToolError("domain 不能为空，例如 example.com")
        cookies = parse_cookie_input(raw_cookies)
        if not cookies:
            raise ToolError("没有解析到任何 Cookie")
        with self._lock:
            data = self._load()
            bucket = data["domains"].get(key, {"cookies": []})
            existing = {c.get("name"): c for c in bucket.get("cookies", [])}
            if merge:
                for cookie in cookies:
                    existing[cookie["name"]] = {**existing.get(cookie["name"], {}), **cookie}
            else:
                existing = {c["name"]: c for c in cookies}
            bucket["cookies"] = list(existing.values())
            bucket["updated_at"] = now_iso()
            data["domains"][key] = bucket
            self._save(data)
        return {"domain": key, "count": len(bucket["cookies"]), "names": [c["name"] for c in bucket["cookies"]]}

    def get(self, domain: str | None = None) -> dict[str, Any]:
        with self._lock:
            data = self._load()
        domains = data.get("domains", {})
        if not domain:
            return {"updated_at": data.get("updated_at", ""), "domains": domains}
        target = _norm_domain(domain)
        matched = {k: v for k, v in domains.items() if _domain_match(k, target)}
        if not matched:
            raise ToolError(
                f"没有存过 {domain} 的 Cookie，可用 set_cookie 写入，或 import_cookie_from_storage_state 导入"
            )
        return {"updated_at": data.get("updated_at", ""), "domains": matched}

    def delete(self, domain: str) -> dict[str, Any]:
        target = _norm_domain(domain)
        with self._lock:
            data = self._load()
            removed = [k for k in data["domains"] if _domain_match(k, target)]
            for key in removed:
                data["domains"].pop(key, None)
            self._save(data)
        return {"removed": removed}

    def to_header(self, domain: str) -> str:
        data = self.get(domain)
        pairs = [f"{c['name']}={c['value']}" for bucket in data["domains"].values() for c in bucket.get("cookies", [])]
        return "; ".join(pairs)

    def import_storage_state(self, file_path: Path, domain: str | None = None) -> dict[str, Any]:
        if not file_path.exists():
            raise ToolError(f"storage_state 文件不存在：{file_path}")
        try:
            state = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolError(f"storage_state 不是合法 JSON：{exc}") from exc
        cookies = state.get("cookies") or []
        if not isinstance(cookies, list) or not cookies:
            raise ToolError("storage_state 里没有 cookies")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for cookie in cookies:
            cookie_domain = _norm_domain(str(cookie.get("domain") or ""))
            if domain and not _domain_match(cookie_domain, domain):
                continue
            grouped.setdefault(cookie_domain or _norm_domain(domain or ""), []).append(cookie)
        if not grouped:
            raise ToolError(f"storage_state 中没有匹配 {domain} 的 Cookie")
        summary = [self.set(key, items, merge=True) for key, items in grouped.items() if key]
        return {"imported": summary}

    def import_from_cdp(self, cdp_url: str, domain: str | None = None) -> dict[str, Any]:
        """通过 Playwright 连接一个已登录的 Chrome（CDP），把 Cookie 拽下来（可选能力）。"""
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise ToolError(
                "未安装 playwright，无法从浏览器直读 Cookie。可改用 storage_state 导入或手工 set_cookie"
            ) from exc

        with sync_playwright() as pw:  # pragma: no cover
            browser = pw.chromium.connect_over_cdp(cdp_url)
            contexts = browser.contexts or []
            if not contexts:
                raise ToolError("已连接浏览器，但没有可用的 context")
            cookies = contexts[0].cookies()
        if domain:
            cookies = [c for c in cookies if _domain_match(_norm_domain(c.get("domain", "")), domain)]
        if not cookies:
            raise ToolError("浏览器里没有匹配的 Cookie（可能还没登录）")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for cookie in cookies:
            grouped.setdefault(_norm_domain(cookie.get("domain", "")), []).append(cookie)
        summary = [self.set(key, items, merge=True) for key, items in grouped.items() if key]
        return {"imported": summary}
