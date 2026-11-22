"""Cookie 存储与管理：供工具/接口读写，也支持导入 Playwright storage_state 与 CDP 导出。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ToolError, ValidationError
from .utils import now_iso

logger = logging.getLogger("agent.cookies")

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")
_UNSAFE_HEADER = {"\r", "\n"}


def _check(value: Any, field_name: str, maxlen: int = 4096) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValidationError(f"{field_name} 不能为空")
    if len(text) > maxlen:
        raise ValidationError(f"{field_name} 过长（>{maxlen}）")
    if any(ch in text for ch in _UNSAFE_HEADER):
        raise ValidationError(f"{field_name} 含非法字符（换行/回车）")
    return text


def normalize_domain(domain: str) -> str:
    host = str(domain or "").strip().to_dict() if False else str(domain or "").strip()
    host = host.lower()
    if "://" in host:
        host = urllib.parse.urlsplit(host).netloc or host
    host = host.split("/")[0]
    host = host.split(":")[0]
    host = host.lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


class CookieStore:
    """domain -> name -> {value, path, domain, secure, http_only, expires, updated_at}"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cookie 存储文件损坏 %s: %s", self.path, exc)

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def set(
        self,
        domain: str,
        name: str,
        value: str,
        path: str = "/",
        secure: bool = False,
        http_only: bool = False,
        expires: float | None = None,
    ) -> dict[str, Any]:
        host = normalize_domain(domain)
        if not host:
            raise ValidationError("domain 不能为空")
        cookie_name = _check(name, "name", 256)
        cookie_value = _check(value, "value")
        entry = {
            "name": cookie_name,
            "value": cookie_value,
            "domain": host,
            "path": path or "/",
            "secure": bool(secure),
            "http_only": bool(http_only),
            "expires": expires,
            "updated_at": now_iso(),
        }
        with self._lock:
            self._data.setdefault(host, {})[cookie_name] = entry
            self._save()
        return entry

    def get(self, domain: str, name: str) -> dict[str, Any]:
        host = normalize_domain(domain)
        with self._lock:
            entry = (self._data.get(host) or {}).get(name)
        if not entry:
            raise NotFoundError(f"Cookie 不存在：{host} / {name}")
        return entry

    def delete(self, domain: str, name: str | None = None) -> int:
        host = normalize_domain(domain)
        with self._lock:
            if name:
                bucket = self._data.get(host) or {}
                if name not in bucket:
                    raise NotFoundError(f"Cookie 不存在：{host} / {name}")
                bucket.pop(name)
                if not bucket:
                    self._data.pop(host, None)
                count = 1
            else:
                count = len(self._data.pop(host, {}) or {})
            self._save()
        return count

    def list(self, domain: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if domain:
                return list((self._data.get(normalize_domain(domain)) or {}).values())
            items: list[dict[str, Any]] = []
            for bucket in self._data.values():
                items.extend(bucket.values())
            items.sort(key=lambda c: (c.get("domain", ""), c.get("name", "")))
            return items

    def domains(self) -> list[str]:
        with self._lock:
            return sorted(self._data.keys())

    def get_cookie_header(self, domain: str) -> str:
        cookies = self.list(domain)
        if not cookies:
            raise NotFoundError(f"{normalize_domain(domain)} 下没有 Cookie")
        return "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    def import_storage_state(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, (str, bytes)):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValidationError(f"storage_state 不是合法 JSON：{exc}") from exc
        if isinstance(payload, dict) and "storage_state" in payload:
            payload = payload["storage_state"]
        if not isinstance(payload, dict) or not isinstance(payload.get("cookies"), list):
            raise ValidationError("需要 Playwright storage_state 格式（包含 cookies 数组）")
        imported = 0
        for item in payload["cookies"]:
            if not isinstance(item, dict):
                continue
            domain = item.get("domain")
            name = item.get("name")
            value = item.get("value")
            if not (domain and name and value is not None):
                continue
            try:
                self.set(
                    domain=domain,
                    name=name,
                    value=value,
                    path=item.get("path") or "/",
                    secure=bool(item.get("secure")),
                    http_only=bool(item.get("httpOnly") or item.get("http_only")),
                    expires=item.get("expires"),
                )
            except ValidationError as exc:
                logger.warning("跳过非法 Cookie %s/%s: %s", domain, name, exc)
                continue
            imported += 1
        return {"imported": imported, "domains": self.domains()}

    def export_storage_state(self) -> dict[str, Any]:
        cookies = []
        for item in self.list():
            cookies.append(
                {
                    "name": item["name"],
                    "value": item["value"],
                    "domain": item["domain"],
                    "path": item.get("path", "/"),
                    "secure": bool(item.get("secure")),
                    "httpOnly": bool(item.get("http_only")),
                    "expires": item.get("expires"),
                }
            )
        return {"cookies": cookies, "origins": []}

    def export_from_cdp(self, cdp_url: str, domain_filter: str | None = None) -> dict[str, Any]:
        """从已开的 Chrome/Chromium CDP 端点拉取 Cookie（依赖 playwright，可选）。"""
        if not cdp_url:
            raise ValidationError("cdp_url 不能为空")
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise ToolError(
                "未安装 playwright，无法从 CDP 取 Cookie。"
                "可在电脑上执行 pip install playwright && playwright install chromium，"
                "或直接浏览器插件导出 JSON 后 POST /api/cookies/import。"
            ) from exc

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=30000)
            contexts = browser.contexts or []
            if not contexts:
                raise ToolError("CDP 端点没有可用的 browser context")
            cookies = contexts[0].cookies()
        if domain_filter:
            host = normalize_domain(domain_filter)
            cookies = [c for c in cookies if host in str(c.get("domain", ""))]
        if not cookies:
            raise NotFoundError("CDP 没有返回 Cookie")
        state = self.import_storage_state({"cookies": cookies})
        return {**state, "cdp_url": cdp_url}
