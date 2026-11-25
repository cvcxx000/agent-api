"""订阅接口：用户看额度 / 兑换；商家生成套餐和兑换码 / 手动开通。"""

from __future__ import annotations

import logging
from typing import Any

from .deploy import env_locked, mode as deploy_mode, set_mode, status as deploy_status
from .deploy import reset as deploy_reset
from .auth import ForbiddenError, clear_cookie, cookie_for, totp_verify, verify_password
from .errors import AppError
from .policy import set_guest_enabled
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
            "codes_enabled": service.codes_enabled() if service.enabled() else False,
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

    def admin_settings(request: Request, params: dict[str, str]) -> Response:
        _admin(request)
        service = _svc(request)
        if request.method == "GET":
            return Response(200, {"ok": True, "codes_enabled": service.codes_enabled()})
        body = request.json()
        if body.get("codes_enabled") is True and body.get("confirm") is not True:
            raise AppError("开启兑换码要二次确认：兑换码等于现金，泄露会被别人兑走")
        value = service.set_codes_enabled(bool(body.get("codes_enabled")))
        logger.warning("兑换码开关被管理员设为：%s", value)
        return Response(200, {"ok": True, "codes_enabled": value})

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

    def setup_init(request: Request, params: dict[str, str]) -> Response:
        auth = getattr(request.runtime, "auth", None)
        if auth is None:
            raise AppError("账号服务没启动")
        if auth.accounts():
            raise ForbiddenError("已经初始化过了；要重来先用管理员账号重置")
        body = request.json()
        if body.get("agree") is not True:
            raise AppError("请先勾选同意《用户协议》和《商家协议》")
        wanted = str(body.get("mode") or "").strip().lower()
        current = deploy_mode()
        if not env_locked() and wanted in ("personal", "merchant") and wanted != current:
            set_mode(wanted)
            current = wanted
        if current == "unset":
            current = "personal"
        result = auth.register(
            {"email": body.get("email"), "password": body.get("password"),
             "name": body.get("name"), "agree": True},
            request,
        )
        auth.set_runtime_config({"allow_register": bool(body.get("allow_register"))})
        set_guest_enabled(bool(body.get("guest_enabled")))
        service = _svc(request)
        if current == "merchant":
            service.set_codes_enabled(bool(body.get("codes_enabled")))
        logger.warning("初始化完成：模式=%s owner=%s", current, result["user"].get("email"))
        headers = {"Set-Cookie": cookie_for(result["token"]), "Cache-Control": "no-store"}
        return Response(200, {
            "ok": True, "mode": current, "user": result["user"],
            "recovery_code": result.get("recovery_code"), "redirect": "/ui",
        }, headers=headers)

    def setup_reset(request: Request, params: dict[str, str]) -> Response:
        auth = getattr(request.runtime, "auth", None)
        if auth is None or not auth.enabled():
            raise AppError("账号体系没开，没什么可重置的")
        ident = auth.require(request)
        if str(ident.get("role") or "") != "owner":
            raise ForbiddenError("只有管理员（owner）能重置")
        body = request.json()
        owner = next((u for u in auth.users() if u.get("id") == ident.get("user_id")), None)
        if owner is None or not verify_password(str(body.get("password") or ""), owner.get("password")):
            raise ForbiddenError("管理员密码不对")
        if str(body.get("phrase") or "").strip().upper() != "RESET":
            raise AppError("确认词要输入 RESET（大写）")
        if owner.get("totp_enabled"):
            step = totp_verify(str(owner.get("totp_secret") or ""), str(body.get("totp") or ""))
            if step is None:
                raise ForbiddenError("这个管理员开了两步验证，要填 6 位验证码才能重置")
        service = getattr(request.runtime, "billing", None)
        auth.reset()
        if service is not None:
            service.reset()
        from .policy import reset as policy_reset

        policy_reset()
        deploy_reset()
        logger.warning("管理员 %s 重置了账号与配置", owner.get("email"))
        return Response(200, {"ok": True, "redirect": "/setup"},
                        headers={"Set-Cookie": clear_cookie(), "Cache-Control": "no-store"})

    router.add("GET", "/api/setup/status", setup_status, auth=False)
    router.add("POST", "/api/setup/init", setup_init, auth=False)
    router.add("POST", "/api/setup/reset", setup_reset, auth=True)
    router.add("POST", "/api/setup/mode", setup_mode, auth=False)
    router.add("GET", "/api/billing/me", me, auth=True)
    router.add("POST", "/api/billing/redeem", redeem, auth=True)
    router.add("GET", "/api/billing/admin/settings", admin_settings, auth=True)
    router.add("POST", "/api/billing/admin/settings", admin_settings, auth=True)
    router.add("GET", "/api/billing/admin/overview", admin_overview, auth=True)
    router.add("POST", "/api/billing/admin/plans", admin_plan, auth=True)
    router.add("GET", "/api/billing/admin/codes", admin_codes, auth=True)
    router.add("POST", "/api/billing/admin/codes", admin_codes, auth=True)
    router.add("POST", "/api/billing/admin/grant", admin_grant, auth=True)
