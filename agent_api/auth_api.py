"""账号相关接口：/api/auth/*（注册 / 登录 / 密钥 / 忘记密码 / Google）。"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from .auth import AuthService, clear_cookie, cookie_for
from .deploy import is_merchant
from .errors import AppError
from .server import Request, Response, Router
from .security import AuthError

logger = logging.getLogger("agent.auth.api")

COOKIE_ATTRS = {"Cache-Control": "no-store"}


def _svc(request: Request) -> AuthService:
    auth = getattr(request.runtime, "auth", None)
    if auth is None:
        raise AppError("账号服务没启动")
    return auth


def _ok(body: dict[str, Any], cookie: str | None = None) -> Response:
    headers = dict(COOKIE_ATTRS)
    if cookie:
        headers["Set-Cookie"] = cookie
    return Response(200, {"ok": True, **body}, headers=headers)


def _me(request: Request) -> dict[str, Any]:
    auth = _svc(request)
    if not auth.enabled():
        raise AuthError("这台设备还没启用账号体系：打开 /login 创建第一个账号")
    ident = auth.require(request)
    if ident is None:
        raise AuthError("未登录")
    if ident.get("via") == "token":
        return {"me": {"id": "", "email": "", "name": "API Token", "role": "admin", "keys": [],
                       "billing_enabled": is_merchant()},
                "identity": ident}
    user = auth.me(str(ident.get("user_id") or ""))
    return {"me": {**user, "via": "cookie", "billing_enabled": is_merchant()}, "identity": ident}


def _uid(auth: AuthService, ident: dict[str, Any]) -> str:
    return str(auth.user_for_identity(ident).get("id") or "")


def mount_auth(router: Router, runtime: Any) -> None:
    # ---- 状态 / 注册 / 登录 ----
    def status(request: Request, params: dict[str, str]) -> Response:
        return Response(200, {"ok": True, **_svc(request).status()})

    def register(request: Request, params: dict[str, str]) -> Response:
        body = request.json()
        result = _svc(request).register(body, request)
        return _ok({"user": result["user"], "recovery_code": result["recovery_code"]}, cookie_for(result["token"]))

    def login(request: Request, params: dict[str, str]) -> Response:
        body = request.json()
        result = _svc(request).login(body, request)
        if result.get("totp_required"):
            return Response(200, {"ok": True, **result}, headers=dict(COOKIE_ATTRS))
        return _ok({"user": result["user"]}, cookie_for(result["token"]))

    def key_login(request: Request, params: dict[str, str]) -> Response:
        body = request.json()
        result = _svc(request).key_login(body, request)
        return _ok({"user": result["user"]}, cookie_for(result["token"]))


    def guest_create(request: Request, params: dict[str, str]) -> Response:
        result = _svc(request).create_guest(request)
        return _ok(
            {k: v for k, v in result.items() if k != "token"},
            cookie_for(result["token"]),
        )

    def guest_unlock(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        body = request.json()
        result = auth.guest_unlock(str(body.get("code") or ""), ident)
        return _ok(result)

    def guest_state(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        uid = _uid(auth, ident)
        return Response(200, {"ok": True, **auth.guest_state(uid)})

    def logout(request: Request, params: dict[str, str]) -> Response:
        _svc(request).logout(request)
        return _ok({"logout": True}, clear_cookie())

    def me(request: Request, params: dict[str, str]) -> Response:
        return Response(200, {"ok": True, **_me(request)})

    def change_password(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        body = request.json()
        cookie = (request.header("cookie") or "")
        token = ""
        for part in cookie.split(";"):
            if part.strip().startswith("agent_auth="):
                token = part.strip().split("=", 1)[1]
                break
        result = auth.change_password(
            _uid(auth, ident), str(body.get("old") or body.get("old_password") or ""),
            str(body.get("new") or body.get("new_password") or ""), keep_token=token,
        )
        return _ok(result)

    # ---- 忘记密码 ----
    def forgot(request: Request, params: dict[str, str]) -> Response:
        body = request.json()
        return Response(200, {"ok": True, **_svc(request).forgot(str(body.get("email") or ""))})

    def reset(request: Request, params: dict[str, str]) -> Response:
        body = request.json()
        result = _svc(request).reset_with_recovery(
            str(body.get("email") or ""), str(body.get("code") or ""),
            str(body.get("new") or body.get("new_password") or ""),
        )
        return _ok(result)

    def google_reset(request: Request, params: dict[str, str]) -> Response:
        body = request.json()
        result = _svc(request).reset_with_ticket(
            str(body.get("ticket") or ""), str(body.get("new") or body.get("new_password") or ""),
        )
        return _ok(result)

    # ---- Google ----
    def google_start(request: Request, params: dict[str, str]) -> Response:
        purpose = str(request.q("purpose") or "login")
        email = str(request.q("email") or "")
        url = _svc(request).google_start(purpose, email)
        return Response(302, None, headers={"Location": url, **COOKIE_ATTRS})

    def google_callback(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        error = request.q("error")
        if error:
            return Response(302, None, headers={
                "Location": f"/login?error={quote('Google 那边取消了：' + error)}", **COOKIE_ATTRS,
            })
        try:
            result = auth.google_callback(str(request.q("code") or ""), str(request.q("state") or ""))
        except AppError as exc:
            return Response(302, None, headers={
                "Location": f"/login?error={quote(exc.message)}", **COOKIE_ATTRS,
            })
        if result.get("reset_ticket"):
            return Response(302, None, headers={"Location": result["redirect"], **COOKIE_ATTRS})
        return Response(302, None, headers={
            "Location": result.get("redirect") or "/",
            "Set-Cookie": cookie_for(result["token"]),
            **COOKIE_ATTRS,
        })

    def google_config(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        return Response(200, {"ok": True, **auth.google_public(), "identity": ident})

    def google_config_save(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        return Response(200, {"ok": True, **auth.google_configure(request.json(), ident)})


    # ---- 身份验证器（Google Authenticator） ----
    def totp_setup(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        return Response(200, {"ok": True, **auth.totp_setup(_uid(auth, ident))})

    def totp_enable(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        body = request.json()
        return Response(200, {"ok": True, **auth.totp_enable(_uid(auth, ident), str(body.get("code") or ""))})

    def totp_disable(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        body = request.json()
        return Response(200, {"ok": True, **auth.totp_disable(
            _uid(auth, ident), str(body.get("code") or ""), str(body.get("password") or ""),
        )})

    def totp_backup(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        body = request.json()
        return Response(200, {"ok": True, **auth.totp_backup_codes(_uid(auth, ident), str(body.get("code") or ""))})

    def login_totp(request: Request, params: dict[str, str]) -> Response:
        body = request.json()
        result = _svc(request).login_totp(str(body.get("ticket") or ""), str(body.get("code") or ""), request)
        return _ok({"user": result["user"]}, cookie_for(result["token"]))

    def reset_totp(request: Request, params: dict[str, str]) -> Response:
        body = request.json()
        result = _svc(request).reset_with_totp(
            str(body.get("email") or ""), str(body.get("code") or ""),
            str(body.get("new") or body.get("new_password") or ""),
        )
        return _ok(result)

    # ---- 密钥 / 设备 ----
    def keys_list(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        return Response(200, {"ok": True, "keys": auth.me(_uid(auth, ident)).get("keys", [])})

    def keys_create(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        body = request.json()
        result = auth.create_key(_uid(auth, ident), str(body.get("name") or ""))
        return Response(200, {"ok": True, **result})

    def keys_delete(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        return Response(200, {"ok": True, **auth.delete_key(_uid(auth, ident), params["id"])})

    def devices(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        return Response(200, {"ok": True, "devices": auth.devices(_uid(auth, ident))})

    def logout_all(request: Request, params: dict[str, str]) -> Response:
        auth = _svc(request)
        ident = auth.require(request)
        if ident.get("via") == "token":
            raise AppError("API Token 通道不能退出账号")
        result = auth.logout_all(_uid(auth, ident))
        return _ok(result, clear_cookie())

    def whoami(request: Request, params: dict[str, str]) -> Response:
        return Response(200, {"ok": True, **_me(request)})

    # 公开：登录页在没凭证时也要能问状态
    router.add("GET", "/api/auth/status", status, auth=False)
    router.add("POST", "/api/auth/register", register, auth=False)
    router.add("POST", "/api/auth/login", login, auth=False)
    router.add("POST", "/api/auth/key-login", key_login, auth=False)
    router.add("POST", "/api/auth/guest", guest_create, auth=False)
    router.add("POST", "/api/auth/forgot", forgot, auth=False)
    router.add("POST", "/api/auth/reset", reset, auth=False)
    router.add("POST", "/api/auth/google/reset", google_reset, auth=False)
    router.add("POST", "/api/auth/login/totp", login_totp, auth=False)
    router.add("POST", "/api/auth/reset/totp", reset_totp, auth=False)
    router.add("GET", "/api/auth/google/start", google_start, auth=False)
    router.add("GET", "/api/auth/google/callback", google_callback, auth=False)

    # 需要登录
    router.add("GET", "/api/auth/me", me, auth=True)
    router.add("GET", "/api/auth/whoami", whoami, auth=True)
    router.add("POST", "/api/auth/logout", logout, auth=True)
    router.add("POST", "/api/auth/logout-all", logout_all, auth=True)
    router.add("POST", "/api/auth/password", change_password, auth=True)
    router.add("POST", "/api/auth/guest/unlock", guest_unlock, auth=True)
    router.add("GET", "/api/auth/guest/state", guest_state, auth=True)
    router.add("GET", "/api/auth/keys", keys_list, auth=True)
    router.add("POST", "/api/auth/keys", keys_create, auth=True)
    router.add("DELETE", "/api/auth/keys/{id}", keys_delete, auth=True)
    router.add("GET", "/api/auth/devices", devices, auth=True)
    router.add("GET", "/api/auth/google/config", google_config, auth=True)
    router.add("POST", "/api/auth/google/config", google_config_save, auth=True)
    router.add("POST", "/api/auth/totp/setup", totp_setup, auth=True)
    router.add("POST", "/api/auth/totp/enable", totp_enable, auth=True)
    router.add("POST", "/api/auth/totp/disable", totp_disable, auth=True)
    router.add("POST", "/api/auth/totp/backup", totp_backup, auth=True)
