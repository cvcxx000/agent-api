"""更新检查接口：owner/admin 才看得到、点得动；游客和商家会员不打扰。"""

from __future__ import annotations

from typing import Any

from . import updater
from .auth import ForbiddenError
from .server import Request, Response, Router


def _admin(request: Request) -> dict[str, Any]:
    auth = getattr(request.runtime, "auth", None)
    if auth is None or not auth.enabled():
        return {"role": "admin", "via": "open"}
    ident = auth.require(request) or {}
    if str(ident.get("role") or "") not in ("owner", "admin"):
        raise ForbiddenError("只有 owner / 管理员能检查并更新服务")
    return ident


def mount_update(router: Router, runtime: Any) -> None:
    def check(request: Request, params: dict[str, str]) -> Response:
        _admin(request)
        return Response(200, updater.check())

    def check_now(request: Request, params: dict[str, str]) -> Response:
        _admin(request)
        return Response(200, updater.check(force=True))

    def apply(request: Request, params: dict[str, str]) -> Response:
        _admin(request)
        return Response(200, updater.apply())

    router.add("GET", "/api/update/check", check, auth=True)
    router.add("POST", "/api/update/check", check_now, auth=True)
    router.add("POST", "/api/update/apply", apply, auth=True)
