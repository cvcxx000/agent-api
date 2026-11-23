"""页面服务：/ui 返回 page.html，css / js 作为独立文件给。

页面和资源都不缓存（no-cache），git pull 之后刷新页面即可生效。
页面本身不带任何第三方依赖，所有 JSON 接口由 ui_api.mount() 挂上。
"""

from __future__ import annotations

from pathlib import Path

UI_DIR = Path(__file__).resolve().parent / "ui"

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


def mount(router) -> None:
    from .server import Response
    from .ui_api import mount as mount_api

    def page(req, params):
        auth = getattr(req.runtime, "auth", None)
        if auth is not None and auth.enabled() and not auth.identity(req):
            return Response(302, None, headers={"Location": "/login"})
        return Response(
            200,
            _load("page.html"),
            headers={"Cache-Control": "no-cache"},
            content_type=_CONTENT_TYPES["page.html"],
        )

    def make(name):
        def handler(req, params):
            return Response(
                200,
                _load(name),
                headers={"Cache-Control": "no-cache"},
                content_type=_CONTENT_TYPES[name],
            )

        return handler

    def login_page(req, params):
        return Response(
            200,
            _load("login.html"),
            headers={"Cache-Control": "no-cache"},
            content_type=_CONTENT_TYPES["login.html"],
        )

    router.add("GET", "/", page, auth=False)
    router.add("GET", "/ui", page, auth=False)
    router.add("GET", "/login", login_page, auth=False)
    router.add("GET", "/ui/login.html", login_page, auth=False)
    router.add("GET", "/ui/app.css", make("app.css"), auth=False)
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
            from .config import settings as _settings

            name = str(params.get("name") or "")
            if not _re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name):
                return Response(404, {"ok": False, "error": "文件名不合法"})
            sandbox = Path(_settings.sandbox_root).resolve()
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
