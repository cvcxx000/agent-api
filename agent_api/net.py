"""零依赖 HTTP 客户端：urllib 包一层，统一错误信息。

只做三件事：发 JSON、收 JSON、下载字节。超时会说清楚是「连不上」还是「太慢」，
错误正文保留前 600 字，方便在界面上直接看到中转站返回了什么。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

UA = "agent-api/0.1 (zero-dep; python-urllib)"


class NetError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _open(method: str, url: str, payload: Any = None, headers: dict[str, str] | None = None,
          timeout: float = 60.0, data: bytes | None = None):
    head = {"User-Agent": UA}
    if headers:
        head.update({k: v for k, v in headers.items() if v})
    body = data
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        head.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method.upper(), headers=head)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        snippet = ""
        try:
            snippet = exc.read().decode("utf-8", "replace")[:600]
        except Exception:  # noqa: BLE001
            pass
        raise NetError(f"HTTP {exc.code}：{snippet or exc.reason}", status=exc.code, body=snippet) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise NetError(f"连不上：{reason}") from exc


def json_request(method: str, url: str, payload: Any = None, headers: dict[str, str] | None = None,
                 timeout: float = 60.0) -> dict[str, Any]:
    with _open(method, url, payload, headers, timeout) as resp:
        raw = resp.read()
    if not raw:
        return {}
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text[:4000]}
    return data if isinstance(data, dict) else {"_list": data}


def get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    return json_request("GET", url, None, headers, timeout)


def post_json(url: str, payload: Any, headers: dict[str, str] | None = None,
              timeout: float = 120.0) -> dict[str, Any]:
    return json_request("POST", url, payload, headers, timeout)


def get_bytes(url: str, headers: dict[str, str] | None = None, timeout: float = 120.0) -> tuple[bytes, str]:
    with _open("GET", url, None, headers, timeout) as resp:
        return resp.read(), (resp.headers.get("Content-Type") or "")


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


def pick(data: dict[str, Any], *keys: str) -> str:
    """从各种形状的返回里挖字符串（中转站字段命名五花八门）。"""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def find_url(obj: Any, depth: int = 0) -> str:
    """递归找第一个像视频/图片地址的字符串。"""
    if depth > 4:
        return ""
    if isinstance(obj, str):
        low = obj.lower()
        if low.startswith("http") and any(ext in low for ext in (".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".webp")):
            return obj
        return ""
    if isinstance(obj, dict):
        for key in ("url", "video_url", "file_url", "output", "result", "download_url", "src"):
            found = find_url(obj.get(key), depth + 1)
            if found:
                return found
        for value in obj.values():
            found = find_url(value, depth + 1)
            if found:
                return found
        return ""
    if isinstance(obj, list):
        for item in obj:
            found = find_url(item, depth + 1)
            if found:
                return found
    return ""
