"""页面服务：/ui 返回 page.html，css / js 作为独立文件给。

页面本身不缓存，静态资源带 ETag，改动后刷新就生效；前端 JS 默认打成一个
app.js（少 8 个请求），服务端再 gzip，手机端首次加载明显快一截。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent / "ui"

_BUNDLE_FILES = (
    "core.js", "sessions.js", "canvas.js", "chat.js",
    "providers.js", "settings.js", "tts.js", "schedules.js", "account.js",
)
_bundle_cache: tuple[tuple[int, int], bytes] | None = None

_CONTENT_TYPES = {
    "page.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "core.js": "application/javascript; charset=utf-8",
    "sessions.js": "application/javascript; charset=utf-8",
    "chat.js": "application/javascript; charset=utf-8",
    "providers.js": "application/javascript; charset=utf-8",
    "settings.js": "application/javascript; charset=utf-8",
    "canvas.js": "application/javascript; charset=utf-8",
    "tts.js": "application/javascript; charset=utf-8",
    "schedules.js": "application/javascript; charset=utf-8",
    "login.html": "text/html; charset=utf-8",
    "setup.html": "text/html; charset=utf-8",
    "setup.js": "application/javascript; charset=utf-8",
    "login.js": "application/javascript; charset=utf-8",
    "account.js": "application/javascript; charset=utf-8",
}

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
}


def _load(name: str) -> bytes:
    """每次请求现读磁盘：git pull 之后刷新页面就生效，不必重启。"""
    return (UI_DIR / name).read_bytes()


def _bundle() -> bytes:
    """所有前端 JS 合成一个文件；按 mtime+size 失效，git pull 后自动重建。"""
    global _bundle_cache
    stamp = tuple((UI_DIR / name).stat().st_mtime_ns for name in _BUNDLE_FILES)
    if _bundle_cache is not None and _bundle_cache[0] == stamp:
        return _bundle_cache[1]
    parts = []
    for name in _BUNDLE_FILES:
        body = _load(name)
        parts.append(f"\n/* {name} */\n".encode("utf-8") + body + b"\n;\n")
    raw = b"".join(parts)
    _bundle_cache = (stamp, raw)
    return raw


def _etag(raw: bytes) -> str:
    return 'W/"' + hashlib.sha1(raw).hexdigest()[:16] + '"'


def _static_response(req, raw: bytes, content_type: str):
    from .server import Response

    tag = _etag(raw)
    headers = {"Cache-Control": "no-cache", "ETag": tag, "Vary": "Accept-Encoding"}
    if (req.header("if-none-match") or "").strip() == tag:
        return Response(304, b"", headers=headers, content_type=content_type)
    return Response(200, raw, headers=headers, content_type=content_type)


def mount(router) -> None:
    from .server import Response
    from .ui_api import mount as mount_api

    def page(req, params):
        auth = getattr(req.runtime, "auth", None)
        if auth is not None and auth.enabled() and not auth.identity(req):
            return Response(302, None, headers={"Location": "/login"})
        return _static_response(req, _load("page.html"), _CONTENT_TYPES["page.html"])

    def make(name):
        def handler(req, params):
            return _static_response(req, _load(name), _CONTENT_TYPES[name])

        return handler

    def bundle(req, params):
        return _static_response(req, _bundle(), "application/javascript; charset=utf-8")

    def terms_page(kind: str):
        def handler(req, params):
            from .terms import html

            return _static_response(req, html(kind).encode("utf-8"), "text/html; charset=utf-8")

        return handler

    def login_page(req, params):
        return _static_response(req, _load("login.html"), _CONTENT_TYPES["login.html"])

    router.add("GET", "/", page, auth=False)
    router.add("GET", "/ui", page, auth=False)
    router.add("GET", "/terms/user", terms_page("user"), auth=False)
    router.add("GET", "/terms/merchant", terms_page("merchant"), auth=False)
    def setup_page(req, params):
        return _static_response(req, _load("setup.html"), "text/html; charset=utf-8")

    router.add("GET", "/setup", setup_page, auth=False)
    router.add("GET", "/ui/setup.html", setup_page, auth=False)
    router.add("GET", "/ui/setup.js", make("setup.js"), auth=False)
    router.add("GET", "/login", login_page, auth=False)
    router.add("GET", "/ui/login.html", login_page, auth=False)
    router.add("GET", "/ui/app.css", make("app.css"), auth=False)
    router.add("GET", "/ui/app.js", bundle, auth=False)
    router.add("GET", "/ui/core.js", make("core.js"), auth=False)
    router.add("GET", "/ui/sessions.js", make("sessions.js"), auth=False)
    router.add("GET", "/ui/chat.js", make("chat.js"), auth=False)
    router.add("GET", "/ui/providers.js", make("providers.js"), auth=False)
    router.add("GET", "/ui/settings.js", make("settings.js"), auth=False)
    router.add("GET", "/ui/tts.js", make("tts.js"), auth=False)
    router.add("GET", "/ui/schedules.js", make("schedules.js"), auth=False)
    router.add("GET", "/ui/canvas.js", make("canvas.js"), auth=False)
    router.add("GET", "/ui/login.js", make("login.js"), auth=False)
    router.add("GET", "/ui/account.js", make("account.js"), auth=False)

    def media_file(subdir: str):
        """只放行 workspace/<subdir>/<文件名>，不让目录穿越。"""
        import re as _re

        def handler(req, params):
            from .security import active_root

            name = str(params.get("name") or "")
            if not _re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name):
                return Response(404, {"ok": False, "error": "文件名不合法"})
            sandbox = active_root()
            target = (sandbox / subdir / name).resolve()
            try:
                target.relative_to(sandbox)
            except ValueError:
                return Response(404, {"ok": False, "error": "路径越界"})
            if not target.is_file():
                return Response(404, {"ok": False, "error": "文件不存在"})
            content_type = _MIME.get(target.suffix.lower(), "application/octet-stream")
            size = target.stat().st_size
            base = {"Cache-Control": "no-cache", "Accept-Ranges": "bytes"}
            raw_range = (req.header("range") or "").strip()
            if raw_range.lower().startswith("bytes="):
                # Range 支持：浏览器放音频/视频要能拖进度、Safari 要见 206 才肯播；
                # 顺便避免把几十 MB 的视频整个读进内存。
                spec = raw_range[6:].split(",")[0].strip()
                left, _, right = spec.partition("-")
                try:
                    if left == "":  # bytes=-500：最后 500 字节
                        start = max(0, size - int(right))
                        end = size - 1
                    else:
                        start = int(left)
                        end = int(right) if right else size - 1
                except ValueError:
                    start, end = 0, -1
                if size == 0 or start >= size or start > end or end < 0:
                    return Response(
                        416, b"",
                        headers={**base, "Content-Range": f"bytes */{size}"},
                    )
                end = min(end, size - 1)
                with open(target, "rb") as fh:
                    fh.seek(start)
                    chunk = fh.read(end - start + 1)
                return Response(
                    206, chunk,
                    headers={**base, "Content-Range": f"bytes {start}-{end}/{size}"},
                    content_type=content_type,
                )
            if size > 8 * 1024 * 1024:
                return Response(
                    200, target.read_bytes(),
                    headers={**base, "Content-Length": str(size)},
                    content_type=content_type,
                )
            return Response(
                200,
                target.read_bytes(),
                headers=base,
                content_type=content_type,
            )

        return handler

    router.add("GET", "/files/media/{name}", media_file("media"), auth=True)
    router.add("GET", "/files/uploads/{name}", media_file("uploads"), auth=True)
    router.add("GET", "/favicon.ico", lambda req, params: Response(204), auth=False)
    mount_api(router)
