"""页面服务：/ui 返回 page.html，css / js 作为独立文件给（浏览器可缓存，改完重启即可）。

页面本身不带任何第三方依赖，所有 JSON 接口由 ui_api.mount() 挂上。
"""

from __future__ import annotations

from functools import lru_cache
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
}


@lru_cache(maxsize=None)
def _load(name: str) -> bytes:
    return (UI_DIR / name).read_bytes()


def mount(router) -> None:
    from .server import Response
    from .ui_api import mount as mount_api

    def page(req, params):
        return Response(200, _load("page.html"), content_type=_CONTENT_TYPES["page.html"])

    def make(name: str):
        def handler(req, params):
            return Response(
                200,
                _load(name),
                headers={"Cache-Control": "no-cache"},
                content_type=_CONTENT_TYPES[name],
            )

        return handler

    router.add("GET", "/", page, auth=False)
    router.add("GET", "/ui", page, auth=False)
    router.add("GET", "/ui/app.css", make("app.css"), auth=False)
    router.add("GET", "/ui/core.js", make("core.js"), auth=False)
    router.add("GET", "/ui/sessions.js", make("sessions.js"), auth=False)
    router.add("GET", "/ui/chat.js", make("chat.js"), auth=False)
    router.add("GET", "/ui/providers.js", make("providers.js"), auth=False)
    router.add("GET", "/ui/settings.js", make("settings.js"), auth=False)
    router.add("GET", "/favicon.ico", lambda req, params: Response(204), auth=False)
    mount_api(router)
