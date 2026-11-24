"""订阅接口：用户看额度 / 兑换；商家生成套餐和兑换码 / 手动开通。"""

from __future__ import annotations

import logging
from typing import Any

from .deploy import env_locked, mode as deploy_mode, set_mode, status as deploy_status
from .auth import ForbiddenError
from .errors import AppError
from .server import Request, Response, Router

logger = logging.getLogger("agent.billing.api")


def _svc(request: Request):
    service = getattr(request.runtime, "billing", None)
    if service is None:
        raise AppError("订阅服务没启动")
    return service


def _ident(request: Request) -> dict[str, Any]:
    auth = getattr(request.runtime, "auth", None)
    if auth is None or not auth.enabled():
        return {"user_id": "", "role": "admin", "via": "token"}
    return auth.require(request) or {}


def _uid(request: Request) -> str:
    ident = _ident(request)
    return str(ident.get("user_id") or "")


def _admin(request: Request) -> dict[str, Any]:
    ident = _ident(request)
    if str(ident.get("role") or "") not in ("owner", "admin"):
        raise ForbiddenError("只有商家 / owner 能管套餐和兑换码")
    return ident


def mount_billing(router: Router, runtime: Any) -> None:
    def setup_status(request: Request, params: dict[str, str]) -> Response:
        auth = getattr(request.runtime, "auth", None)
        has_users = bool(auth.accounts()) if auth is not None else False
        return Response(200, {
            "ok": True,
            "needs_choice": deploy_mode() == "unset" and not has_users and not env_locked(),
            "has_users": has_users,
            **deploy_status(),
        })

    def setup_mode(request: Request, params: dict[str, str]) -> Response:
        auth = getattr(request.runtime, "auth", None)
        if auth is not None and auth.accounts():
            raise ForbiddenError("已经有正式账号了，模式在 .env 的 DEPLOY_MODE 里改")
        body = request.json()
        try:
            value = set_mode(str(body.get("mode") or ""))
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        logger.info("部署模式设为：%s", value)
        return Response(200, {"ok": True, "mode": value})

    def me(request: Request, params: dict[str, str]) -> Response:
        service = _svc(request)
        ident = _ident(request)
        role = str(ident.get("role") or "")
        uid = str(ident.get("user_id") or "")
        account = service.account(uid) if uid else {}
        return Response(200, {
            "ok": True,
            "mode": deploy_mode(),
            "enabled": service.enabled(),
            "role": role,
            "account": account,
            "plans": service.plans() if service.enabled() else [],
        })

    def redeem(request: Request, params: dict[str, str]) -> Response:
        service = _svc(request)
        if not service.enabled():
            raise AppError("当前是个人版，不需要订阅；要卖订阅请把 DEPLOY_MODE 设成 merchant")
        ident = _ident(request)
        role = str(ident.get("role") or "")
        if role in ("guest", ""):
            raise ForbiddenError("游客不能兑换订阅，注册正式账号再用")
        body = request.json()
        account = service.redeem(str(ident.get("user_id") or ""), str(body.get("code") or ""))
        logger.info("账号 %s 兑换了订阅", ident.get("user_id"))
        return Response(200, {"ok": True, "account": account})

    def admin_overview(request: Request, params: dict[str, str]) -> Response:
        _admin(request)
        service = _svc(request)
        if not service.enabled():
            raise AppError("个人版没有订阅账户")
        auth = request.runtime.auth
        rows = []
        for user in auth.accounts():
            account = service.account(str(user.get("id") or ""))
            rows.append({
                "id": user.get("id"), "email": user.get("email"), "name": user.get("name"),
                "role": user.get("role"), "account": account,
            })
        return Response(200, {"ok": True, "accounts": rows, "codes": service.list_codes(20)})

    def admin_plan(request: Request, params: dict[str, str]) -> Response:
        _admin(request)
        service = _svc(request)
        plan = service.upsert_plan(request.json())
        return Response(200, {"ok": True, "plan": plan, "plans": service.plans()})

    def admin_codes(request: Request, params: dict[str, str]) -> Response:
        _admin(request)
        service = _svc(request)
        if request.method == "GET":
            return Response(200, {"ok": True, "codes": service.list_codes(request.q_int("limit", 50, lo=1, hi=200))})
        body = request.json()
        codes = service.generate_codes(
            str(body.get("plan_id") or ""), int(body.get("count") or 1), str(body.get("note") or ""),
        )
        logger.info("生成了 %d 个兑换码", len(codes))
        return Response(200, {"ok": True, "codes": codes})

    def admin_grant(request: Request, params: dict[str, str]) -> Response:
        _admin(request)
        service = _svc(request)
        body = request.json()
        account = service.grant(
            str(body.get("user_id") or ""), str(body.get("plan_id") or ""),
            int(body["days"]) if body.get("days") else None,
            int(body["messages"]) if body.get("messages") else None,
        )
        return Response(200, {"ok": True, "account": account})

    router.add("GET", "/api/setup/status", setup_status, auth=False)
    router.add("POST", "/api/setup/mode", setup_mode, auth=False)
    router.add("GET", "/api/billing/me", me, auth=True)
    router.add("POST", "/api/billing/redeem", redeem, auth=True)
    router.add("GET", "/api/billing/admin/overview", admin_overview, auth=True)
    router.add("POST", "/api/billing/admin/plans", admin_plan, auth=True)
    router.add("GET", "/api/billing/admin/codes", admin_codes, auth=True)
    router.add("POST", "/api/billing/admin/codes", admin_codes, auth=True)
    router.add("POST", "/api/billing/admin/grant", admin_grant, auth=True)
