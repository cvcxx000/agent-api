"""账号与登录：注册 / 密码登录 / 密钥登录 / 忘记密码 / Google 身份认证。

原则（和整个项目一致：零第三方依赖、手机 Termux 上直接跑）：

- 密码用标准库 `hashlib.scrypt` 存哈希，不存明文、不用 pip 装 bcrypt；
- 登录态是自己签发的随机 token，放 **HttpOnly Cookie**（`agent_auth`），
  浏览器 JS 读不到；服务端只存 token 的 sha256；
- 「密钥登录」给脚本 / 终端 / 别的设备：`ak_…` 随机串，也只存哈希，
  `Authorization: Bearer ak_…`、`X-API-Token: ak_…`、`X-API-Key: ak_…` 都认；
- Google 登录走标准 OAuth 2.0（授权码 + userinfo），只用 urllib；
- 忘记密码不依赖邮件：用 **Google 身份认证** 或注册时给的 **恢复码** 重置；
- 登录失败 5 次锁 15 分钟；认证动作都写日志。
- 默认 `AUTH_MODE=on`：没账号时第一屏是「创建第一个账号」，建完即生效；
  想回到「有账号才要求登录」的旧行为，设 `AUTH_MODE=auto`。

边界：正式账号是「一台设备一个主人」的工具，共用 workspace 与同一批会话；
只有游客是临时的例外 —— 每个游客有独立目录、独立会话归属，到期自动清空。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import struct
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .deploy import env_locked, mode as deploy_mode, status as deploy_status
from .errors import AppError
from .net import NetError, UA, json_request
from .security import AuthError
from .utils import new_id

logger = logging.getLogger("agent.auth")

COOKIE_NAME = "agent_auth"
SESSION_SECONDS = 30 * 24 * 3600
LOCK_AFTER = 5          # 连续错几次就锁
LOCK_SECONDS = 15 * 60  # 锁多久
STATE_SECONDS = 10 * 60
TICKET_SECONDS = 10 * 60
MAX_SESSIONS_PER_USER = 20

GUEST_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _env_int(name: str, default: int, lo: int = 1, hi: int = 10**9) -> int:
    try:
        value = int(str(os.getenv(name) or "").strip() or default)
    except ValueError:
        value = default
    return max(lo, min(hi, value))


def guest_enabled() -> bool:
    raw = (os.getenv("GUEST_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def guest_ttl_hours() -> int:
    return _env_int("GUEST_TTL_HOURS", 24, 1, 24 * 30)


def guest_quota_mb() -> int:
    return _env_int("GUEST_QUOTA_MB", 100, 1, 10240)


def guest_rate_seconds() -> int:
    return _env_int("GUEST_RATE_SECONDS", 60, 1, 3600)


def guest_dir(gid: str) -> Path:
    return Path(settings.sandbox_root) / "guests" / str(gid)


def dir_bytes(path: Path) -> int:
    total = 0
    try:
        for item in Path(path).rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _new_guest_code() -> str:
    raw = "".join(secrets.choice(GUEST_CODE_ALPHABET) for _ in range(8))
    return raw[:4] + "-" + raw[4:]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

# 三个地址可用环境变量覆盖：写测试 / 对接内网 Google 兼容端点时用
GOOGLE_AUTH_URL = os.getenv("GOOGLE_AUTH_URL") or "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = os.getenv("GOOGLE_TOKEN_URL") or "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = os.getenv("GOOGLE_USERINFO_URL") or "https://openidconnect.googleapis.com/v1/userinfo"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class TooManyError(AppError):
    status_code = 429
    code = "too_many_requests"


class GuestRateError(TooManyError):
    code = "guest_rate_limit"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_time(value: Any) -> str:
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------- 密码 ----------------
def hash_password(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    return {"algo": "scrypt", "n": 16384, "r": 8, "p": 1, "salt": salt.hex(), "hash": digest.hex()}


def verify_password(password: str, record: dict[str, Any] | None) -> bool:
    if not record or not record.get("hash") or not record.get("salt"):
        return False
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(str(record["salt"])),
            n=int(record.get("n") or 16384), r=int(record.get("r") or 8),
            p=int(record.get("p") or 1), dklen=32, maxmem=64 * 1024 * 1024,
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), str(record["hash"]))


def check_password_strength(password: str, email: str = "") -> None:
    if len(password) < 8:
        raise AppError("密码至少 8 位（建议 12 位以上，别用生日和手机号）")
    if len(password) > 200:
        raise AppError("密码太长（200 位以内）")
    if email and password.lower() in {email.lower(), email.split("@")[0].lower()}:
        raise AppError("密码不能和邮箱一样")
    if len(set(password)) == 1:
        raise AppError("密码不能全是同一个字符")


def _new_recovery_code() -> str:
    raw = secrets.token_hex(9).upper()
    return "-".join(raw[i:i + 6] for i in range(0, 18, 6))


def _norm_code(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(code or "")).upper()


def _cookie_header(token: str) -> str:
    return f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Lax"


def _clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def _parse_cookie(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(header or "").split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        out[key.strip()] = value.strip()
    return out


def _valid_email(email: str) -> str:
    email = str(email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AppError("邮箱格式不对，例如 you@gmail.com")
    if len(email) > 120:
        raise AppError("邮箱太长了")
    return email


class AuthService:
    def __init__(self, settings: Any):
        self.settings = settings
        self._lock = threading.RLock()

    # ---------------- 存储 ----------------
    @property
    def path(self) -> Path:
        return Path(self.settings.data_dir) / "auth.json"

    def _load(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"users": [], "sessions": [], "states": [], "tickets": [], "config": {}}
        path = self.path
        if not path.exists():
            return empty
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("auth.json 读不出来，当作空账号库")
            return empty
        if not isinstance(data, dict):
            return empty
        for key, default in empty.items():
            if not isinstance(data.get(key), type(default)):
                data[key] = default
        real = [u for u in data["users"] if not u.get("guest")]
        if real and not any(u.get("role") == "owner" for u in real):
            oldest = min(real, key=lambda u: str(u.get("created_at") or ""))
            oldest["role"] = "owner"
            self._save(data)
            logger.info("账号库里没有 owner，把最早注册的 %s 提成 owner", oldest.get("email"))
        return data

    def _save(self, data: dict[str, Any]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)

    @staticmethod
    def _prune(data: dict[str, Any]) -> bool:
        """清掉过期的会话 / state / 票；返回是否真的删了东西（没必要就不写盘）。"""
        now = time.time()
        changed = False
        for key in ("sessions", "states", "tickets"):
            kept = [s for s in data[key] if float(s.get("expires") or 0) > now]
            if len(kept) != len(data[key]):
                changed = True
            data[key] = kept

        expired_guests = [
            str(u.get("id"))
            for u in data.get("users", [])
            if u.get("guest") and float(u.get("guest_expires") or 0) <= now
        ]
        if expired_guests:
            changed = True
            data["users"] = [u for u in data.get("users", []) if str(u.get("id")) not in expired_guests]
            data["sessions"] = [s for s in data["sessions"] if str(s.get("user_id")) not in expired_guests]
            for gid in expired_guests:
                shutil.rmtree(guest_dir(gid), ignore_errors=True)
        return changed

    # ---------------- 状态 ----------------
    def _mode(self) -> str:
        return (os.getenv("AUTH_MODE") or "on").strip().lower()

    def accounts(self) -> list[dict[str, Any]]:
        """正式账号（不含游客）。游客是临时身份，不占 owner、不影响注册开关。"""
        return [u for u in self.users() if not u.get("guest")]

    def users(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._load()["users"])

    def has_users(self) -> bool:
        return bool(self.accounts())

    def enabled(self) -> bool:
        mode = self._mode()
        if mode in {"off", "false", "0", "no"}:
            return False
        if mode in {"on", "true", "1", "yes"}:
            return True
        return self.has_users() or bool(self.settings.api_token)

    def config(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._load().get("config") or {})

    def register_open(self) -> bool:
        if deploy_mode() == "merchant":
            return True
        cfg = self.config()
        if not self.has_users():
            return True
        return bool(cfg.get("allow_register"))

    def status(self) -> dict[str, Any]:
        cfg = self.config()
        users = self.accounts()
        return {
            "enabled": self.enabled(),
            "users": len(users),
            "guests": sum(1 for u in self.users() if u.get("guest")),
            "guest_enabled": guest_enabled(),
            "guest_ttl_hours": guest_ttl_hours(),
            "guest_quota_mb": guest_quota_mb(),
            "guest_rate_seconds": guest_rate_seconds(),
            "mode": deploy_mode(),
            "mode_unset": deploy_mode() == "unset",
            "mode_env_locked": env_locked(),
            "deploy": deploy_status(),
            "setup_needed": not users,
            "register_open": self.register_open(),
            "allow_register": bool(cfg.get("allow_register")),
            "google_on": bool(cfg.get("google_client_id") and cfg.get("google_client_secret")),
            "totp_users": sum(1 for u in users if u.get("totp_enabled")),
            "google_client_id": str(cfg.get("google_client_id") or ""),
            "redirect_uri": self.redirect_uri(),
        }

    # ---------------- 认证 ----------------
    def identity(self, request: Any) -> dict[str, Any] | None:
        if not self.enabled():
            return None
        token_header = request.header("x-api-token") or ""
        if self.settings.api_token and token_header and hmac.compare_digest(token_header, self.settings.api_token):
            return {"user_id": "", "email": "", "name": "API Token", "role": "admin", "via": "token"}

        cookie = _parse_cookie(request.header("cookie")).get(COOKIE_NAME, "")
        if cookie:
            user = self._session_user(cookie)
            if user:
                return self._ident(user, "cookie")

        auth_header = request.header("authorization") or ""
        key = ""
        if auth_header.lower().startswith("bearer "):
            key = auth_header[7:].strip()
        if not key:
            key = request.header("x-api-key") or ""
        if not key and token_header.startswith("ak_"):
            key = token_header
        if key:
            user = self._key_user(key)
            if user:
                return self._ident(user, "key")
        return None

    def require(self, request: Any) -> dict[str, Any] | None:
        if not self.enabled():
            return None
        ident = self.identity(request)
        if ident:
            try:
                request.identity = ident  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            return ident
        raise AuthError("未登录：请打开 /login 登录，或用 API 密钥（Authorization: Bearer ak_…）")

    @staticmethod
    def _ident(user: dict[str, Any], via: str) -> dict[str, Any]:
        return {
            "user_id": str(user.get("id") or ""),
            "email": str(user.get("email") or ""),
            "name": str(user.get("name") or ""),
            "role": str(user.get("role") or "user"),
            "via": via,
        }

    def _session_user(self, token: str) -> dict[str, Any] | None:
        digest = _sha(token)
        with self._lock:
            data = self._load()
            pruned = self._prune(data)
            found = None
            for session in data["sessions"]:
                if hmac.compare_digest(str(session.get("hash") or ""), digest):
                    found = session
                    break
            if found is None:
                if pruned:
                    self._save(data)
                return None
            uid = str(found.get("user_id") or "")
            user = next((u for u in data["users"] if u.get("id") == uid), None)
            if pruned:
                self._save(data)
        return user

    def _key_user(self, key: str) -> dict[str, Any] | None:
        if not key.startswith("ak_"):
            return None
        digest = _sha(key)
        with self._lock:
            data = self._load()
            found = None
            for user in data["users"]:
                for item in user.get("api_keys") or []:
                    if hmac.compare_digest(str(item.get("hash") or ""), digest):
                        item["last_used"] = _now()
                        found = user
                        break
                if found:
                    break
            if found:
                self._save(data)
        return found

    # ---------------- 注册 / 登录 ----------------
    def _issue_session(self, user: dict[str, Any], request: Any) -> str:
        token = secrets.token_urlsafe(32)
        entry = {
            "id": new_id("ses"),
            "hash": _sha(token),
            "user_id": user["id"],
            "created": _now(),
            "expires": time.time() + SESSION_SECONDS,
            "ip": str(getattr(request, "client_ip", "") or ""),
            "ua": str((request.header("user-agent") or "")[:120]),
        }
        with self._lock:
            data = self._load()
            self._prune(data)
            own = [s for s in data["sessions"] if s.get("user_id") == user["id"]]
            if len(own) >= MAX_SESSIONS_PER_USER:
                oldest = min(own, key=lambda s: float(s.get("expires") or 0))
                data["sessions"].remove(oldest)
            data["sessions"].append(entry)
            for item in data["users"]:
                if item.get("id") == user["id"]:
                    item["last_login"] = _now()
            self._save(data)
        logger.info("登录成功：%s（%s）", user.get("email"), entry["id"])
        return token

    def register(self, body: dict[str, Any], request: Any) -> dict[str, Any]:
        email = _valid_email(str(body.get("email") or ""))
        password = str(body.get("password") or "")
        name = str(body.get("name") or "").strip()[:40] or email.split("@")[0]
        check_password_strength(password, email)
        with self._lock:
            data = self._load()
            if any(u.get("email") == email for u in data["users"]):
                raise AppError("这个邮箱已经注册过了，直接登录")
            real_users = [u for u in data["users"] if not u.get("guest")]
            if real_users and not self.register_open():
                raise ForbiddenError("注册已关闭：这台设备只允许一个账号。登录后在「账号与安全」里可以打开注册")
            recovery = _new_recovery_code()
            user = {
                "id": new_id("usr"),
                "email": email,
                "name": name,
                "role": "owner" if not real_users else "user",
                "password": hash_password(password),
                "google_sub": "",
                "google_email": "",
                "totp_secret": "",
                "totp_enabled": False,
                "totp_last_step": 0,
                "totp_pending": "",
                "backup_codes": [],
                "recovery_hash": _sha(_norm_code(recovery)),
                "api_keys": [],
                "created_at": _now(),
                "last_login": "",
                "failed": 0,
                "locked_until": 0,
            }
            data["users"].append(user)
            self._save(data)
        token = self._issue_session(user, request)
        logger.info("注册新账号：%s（%s）", email, user["role"])
        return {"user": self.public_user(user), "token": token, "recovery_code": recovery}

    # ---------------- 游客（临时 / 匿名） ----------------
    def create_guest(self, request: Any) -> dict[str, Any]:
        """开一个只属于自己的临时身份：独立目录、不能升级、到期自动消失。"""
        if not self.enabled():
            raise AppError("账号体系没开，游客身份无处附着：先把 AUTH_MODE 设成 on")
        if not guest_enabled():
            raise ForbiddenError("这台设备关掉了游客登录（GUEST_ENABLED=0）")
        now = time.time()
        ttl = guest_ttl_hours()
        quota = guest_quota_mb()
        gid = "gst_" + secrets.token_urlsafe(9)
        code = _new_guest_code()
        user = {
            "id": gid,
            "email": "",
            "name": "游客",
            "role": "guest",
            "password": "",
            "google_sub": "",
            "google_email": "",
            "totp_secret": "",
            "totp_enabled": False,
            "totp_last_step": 0,
            "totp_pending": "",
            "backup_codes": [],
            "recovery_hash": "",
            "api_keys": [],
            "created_at": _now(),
            "last_login": _now(),
            "failed": 0,
            "locked_until": 0,
            "guest": True,
            "guest_expires": now + ttl * 3600,
            "guest_unlocked": False,
            "guest_quota_mb": quota,
            "guest_unlock": _sha(_norm_code(code)),
            "last_chat": 0,
        }
        with self._lock:
            data = self._load()
            self._prune(data)
            data["users"].append(user)
            self._save(data)
        guest_dir(gid).mkdir(parents=True, exist_ok=True)
        token = self._issue_session(user, request)
        logger.info("开了游客身份：%s（%s 小时 / %sMB）", gid, ttl, quota)
        return {
            "user": self.public_user(user),
            "token": token,
            "unlock_code": code,
            "ttl_hours": ttl,
            "quota_mb": quota,
            "rate_seconds": guest_rate_seconds(),
            "expires_at": _fmt_time(user["guest_expires"]),
            "notice": "这是临时匿名身份：只活在这一个 ID 里，不能升级成正式账号，到期自动清空。",
        }

    def guest_state(self, user_id: str) -> dict[str, Any]:
        used = dir_bytes(guest_dir(user_id))
        with self._lock:
            user = next((u for u in self._load()["users"] if u.get("id") == user_id), None)
        if not user or not user.get("guest"):
            raise AuthError("游客身份已失效")
        quota = int(user.get("guest_quota_mb") or guest_quota_mb()) * 1024 * 1024
        pct = int(min(100, used * 100 / quota)) if quota else 0
        return {
            "user": self.public_user(user),
            "used_bytes": used,
            "quota_bytes": quota,
            "used_mb": round(used / (1024 * 1024), 2),
            "quota_mb": int(user.get("guest_quota_mb") or guest_quota_mb()),
            "percent": pct,
            "unlocked": bool(user.get("guest_unlocked")),
            "rate_seconds": guest_rate_seconds(),
        }

    def guest_check(self, user_id: str, chat: bool = False) -> dict[str, Any]:
        """校验游客是否还在有效期 / 是否超过一分钟一条 / 空间是否已满。"""
        now = time.time()
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("id") == user_id), None)
            if user is None or not user.get("guest"):
                raise AuthError("游客身份已失效，重新点一次「游客试用」")
            if float(user.get("guest_expires") or 0) <= now:
                gid = str(user.get("id"))
                data["users"] = [u for u in data["users"] if u.get("id") != gid]
                data["sessions"] = [s for s in data["sessions"] if s.get("user_id") != gid]
                self._save(data)
                raise AuthError("游客体验已结束（到期自动清空），重新点一次「游客试用」开新的")
            if chat:
                quota_bytes = int(user.get("guest_quota_mb") or guest_quota_mb()) * 1024 * 1024
                if dir_bytes(guest_dir(str(user.get("id"))) ) >= quota_bytes:
                    raise AppError(f"游客空间 {quota_bytes // 1024 // 1024}MB 已经用满：删点自己的文件，或注册正式账号")
                interval = guest_rate_seconds()
                last = float(user.get("last_chat") or 0)
                wait = int(interval - (now - last))
                if wait > 0:
                    raise GuestRateError(f"游客每分钟只能发一条：还要等 {wait} 秒（正式账号不限）")
                user["last_chat"] = now
                self._save(data)
        return self.guest_state(user_id)

    def guest_unlock(self, code: str, ident: dict[str, Any]) -> dict[str, Any]:
        user = self.user_for_identity(ident)
        if not user.get("guest"):
            raise ForbiddenError("只有游客需要解锁码")
        if not hmac.compare_digest(str(user.get("guest_unlock") or ""), _sha(_norm_code(code))):
            raise AuthError("解锁码不对")
        if user.get("guest_unlocked"):
            return {"ok": True, "already": True}
        with self._lock:
            data = self._load()
            item = next((u for u in data["users"] if u.get("id") == user.get("id")), None)
            if item is None:
                raise AuthError("游客身份已失效")
            item["guest_unlocked"] = True
            self._save(data)
        logger.info("游客 %s 解锁了 Python / 终端", user.get("id"))
        return {"ok": True, "already": False}

    def login(self, body: dict[str, Any], request: Any) -> dict[str, Any]:
        email = _valid_email(str(body.get("email") or ""))
        password = str(body.get("password") or "")
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("email") == email), None)
            if user is None:
                raise AuthError("邮箱或密码不对")
            locked_until = float(user.get("locked_until") or 0)
            if locked_until > time.time():
                minutes = int((locked_until - time.time()) // 60) + 1
                raise TooManyError(f"密码错太多次了，锁了 15 分钟；还有 {minutes} 分钟")
            if not user.get("password"):
                raise AuthError("这个账号是 Google 登录建的，没有密码；用 Google 登录或走「忘记密码」")
            if not verify_password(password, user.get("password")):
                user["failed"] = int(user.get("failed") or 0) + 1
                if user["failed"] >= LOCK_AFTER:
                    user["locked_until"] = time.time() + LOCK_SECONDS
                    user["failed"] = 0
                    logger.warning("账号 %s 连续登录失败，锁 15 分钟", email)
                self._save(data)
                raise AuthError("邮箱或密码不对")
            user["failed"] = 0
            user["locked_until"] = 0
            if user.get("totp_enabled"):
                ticket = self._totp_ticket(user["id"], data)
                return {"totp_required": True, "ticket": ticket, "user": {"email": user.get("email"), "name": user.get("name")}}
            self._save(data)
        token = self._issue_session(user, request)
        return {"user": self.public_user(user), "token": token}

    def key_login(self, body: dict[str, Any], request: Any) -> dict[str, Any]:
        key = str(body.get("key") or "").strip()
        if not key:
            raise AppError("key 不能为空（ak_ 开头的那串）")
        user = self._key_user(key)
        if not user:
            raise AuthError("密钥无效，或者已经被吊销")
        token = self._issue_session(user, request)
        return {"user": self.public_user(user), "token": token}

    def logout(self, request: Any) -> None:
        token = _parse_cookie(request.header("cookie")).get(COOKIE_NAME, "")
        if not token:
            return
        digest = _sha(token)
        with self._lock:
            data = self._load()
            data["sessions"] = [s for s in data["sessions"] if not hmac.compare_digest(str(s.get("hash") or ""), digest)]
            self._save(data)

    def logout_all(self, user_id: str, keep: str = "") -> dict[str, Any]:
        digest = _sha(keep) if keep else ""
        with self._lock:
            data = self._load()
            before = len(data["sessions"])
            data["sessions"] = [
                s for s in data["sessions"]
                if s.get("user_id") != user_id or (digest and hmac.compare_digest(str(s.get("hash") or ""), digest))
            ]
            self._save(data)
        return {"ok": True, "revoked": before - len(data["sessions"])}

    def _guest_guard(self, user_id: str, action: str) -> None:
        user = next((u for u in self.users() if u.get("id") == user_id), None)
        if user and user.get("guest"):
            raise ForbiddenError(f"游客账号不能{action}：临时身份不保存这些，注册正式账号才能用")

    def change_password(self, user_id: str, old: str, new: str, keep_token: str = "") -> dict[str, Any]:
        self._guest_guard(user_id, "改密码")
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("id") == user_id), None)
            if user is None:
                raise AuthError("登录状态失效，重新登录")
            if user.get("password") and not verify_password(old, user.get("password")):
                raise AuthError("原密码不对")
            check_password_strength(new, str(user.get("email") or ""))
            user["password"] = hash_password(new)
            recovery = _new_recovery_code()
            user["recovery_hash"] = _sha(_norm_code(recovery))
            uid = user["id"]
            keep = _sha(keep_token) if keep_token else ""
            data["sessions"] = [
                s for s in data["sessions"]
                if s.get("user_id") != uid or (keep and hmac.compare_digest(str(s.get("hash") or ""), keep))
            ]
            self._save(data)
        logger.info("账号 %s 改了密码", user_id)
        return {"ok": True, "recovery_code": recovery}

    # ---------------- 忘记密码 ----------------
    def forgot(self, email_raw: str) -> dict[str, Any]:
        email = _valid_email(email_raw)
        user = next((u for u in self.users() if u.get("email") == email), None)
        if user is None:
            return {"exists": False, "methods": [], "hint": "没有这个邮箱的账号"}
        methods: list[str] = []
        if user.get("google_sub"):
            methods.append("google")
        if user.get("totp_enabled"):
            methods.append("authenticator")
        if user.get("recovery_hash"):
            methods.append("recovery")
        return {
            "exists": True,
            "email": email,
            "name": str(user.get("name") or ""),
            "methods": methods,
            "google_on": bool(self.config().get("google_client_id")),
        }

    def reset_with_recovery(self, email_raw: str, code: str, new_password: str) -> dict[str, Any]:
        email = _valid_email(email_raw)
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("email") == email), None)
            if user is None or not user.get("recovery_hash"):
                raise AuthError("恢复码不对")
            if not hmac.compare_digest(str(user["recovery_hash"]), _sha(_norm_code(code))):
                raise AuthError("恢复码不对")
            check_password_strength(new_password, email)
            user["password"] = hash_password(new_password)
            new_code = _new_recovery_code()
            user["recovery_hash"] = _sha(_norm_code(new_code))
            user["failed"] = 0
            user["locked_until"] = 0
            uid = user["id"]
            data["sessions"] = [s for s in data["sessions"] if s.get("user_id") != uid]
            self._save(data)
        logger.info("账号 %s 用恢复码重置了密码（所有设备已下线）", email)
        return {"ok": True, "email": email, "recovery_code": new_code}

    def reset_with_ticket(self, ticket: str, new_password: str) -> dict[str, Any]:
        digest = _sha(str(ticket or "").strip())
        with self._lock:
            data = self._load()
            self._prune(data)
            found = next(
                (t for t in data["tickets"]
                 if hmac.compare_digest(str(t.get("hash") or ""), digest) and t.get("purpose") == "reset"),
                None,
            )
            if found is None:
                raise AuthError("这个重置链接已失效，重新走一遍「忘记密码」")
            user = next((u for u in data["users"] if u.get("id") == found.get("user_id")), None)
            if user is None:
                raise AuthError("账号不存在了")
            check_password_strength(new_password, str(user.get("email") or ""))
            user["password"] = hash_password(new_password)
            new_code = _new_recovery_code()
            user["recovery_hash"] = _sha(_norm_code(new_code))
            user["failed"] = 0
            user["locked_until"] = 0
            uid = user["id"]
            data["tickets"] = [t for t in data["tickets"] if t is not found]
            data["sessions"] = [s for s in data["sessions"] if s.get("user_id") != uid]
            self._save(data)
        logger.info("账号 %s 用 Google 身份认证重置了密码", user.get("email"))
        return {"ok": True, "email": str(user.get("email") or ""), "recovery_code": new_code}


    # ---------------- TOTP：Google Authenticator ----------------
    def _user_record(self, user_id: str) -> dict[str, Any]:
        user = next((u for u in self.users() if u.get("id") == user_id), None)
        if user is None:
            raise AuthError("登录状态失效，重新登录")
        return user

    def _totp_ticket(self, user_id: str, data: dict[str, Any]) -> str:
        ticket = secrets.token_urlsafe(24)
        self._prune(data)
        data["tickets"].append({
            "hash": _sha(ticket), "user_id": user_id, "purpose": "totp-login",
            "expires": time.time() + 5 * 60, "tries": 0,
        })
        self._save(data)
        return ticket

    def totp_setup(self, user_id: str) -> dict[str, Any]:
        self._guest_guard(user_id, "绑定身份验证器")
        user = self._user_record(user_id)
        secret = new_totp_secret()
        with self._lock:
            data = self._load()
            for item in data["users"]:
                if item.get("id") == user_id:
                    item["totp_pending"] = secret
            self._save(data)
        email = str(user.get("email") or "")
        uri = otpauth_uri(secret, email)
        from .qr import svg

        return {"secret": secret, "uri": uri, "svg": svg(uri), "account": email, "issuer": "Agent"}

    def totp_enable(self, user_id: str, code: str) -> dict[str, Any]:
        self._guest_guard(user_id, "绑定身份验证器")
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("id") == user_id), None)
            if user is None:
                raise AuthError("登录状态失效，重新登录")
            secret = str(user.get("totp_pending") or "")
            if not secret:
                raise AppError("先生成密钥（点「开始绑定」）")
            step = totp_verify(secret, code)
            if step is None:
                raise AppError("验证码不对：看手机上的 6 位数字，注意别过期（30 秒一换）")
            backup = [_new_backup_code() for _ in range(BACKUP_CODE_COUNT)]
            user["totp_secret"] = secret
            user["totp_enabled"] = True
            user["totp_last_step"] = step
            user["totp_pending"] = ""
            user["backup_codes"] = [{"hash": _sha(_norm_backup(c)), "used": False} for c in backup]
            self._save(data)
        logger.info("账号 %s 启用了身份验证器", user_id)
        return {"ok": True, "backup_codes": backup}

    def totp_disable(self, user_id: str, code: str, password: str = "") -> dict[str, Any]:
        self._guest_guard(user_id, "关闭身份验证器")
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("id") == user_id), None)
            if user is None:
                raise AuthError("登录状态失效，重新登录")
            if not user.get("totp_enabled"):
                raise AppError("还没启用身份验证器")
            ok = False
            if password and user.get("password") and verify_password(password, user.get("password")):
                ok = True
            if not ok:
                if self._consume_totp(user, code):
                    ok = True
            if not ok:
                raise AppError("验证码或密码不对")
            user["totp_enabled"] = False
            user["totp_secret"] = ""
            user["totp_pending"] = ""
            user["backup_codes"] = []
            self._save(data)
        logger.info("账号 %s 关掉了身份验证器", user_id)
        return {"ok": True}

    def totp_backup_codes(self, user_id: str, code: str) -> dict[str, Any]:
        self._guest_guard(user_id, "生成备用码")
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("id") == user_id), None)
            if user is None:
                raise AuthError("登录状态失效，重新登录")
            if not user.get("totp_enabled"):
                raise AppError("还没启用身份验证器")
            if not self._consume_totp(user, code):
                raise AppError("验证码不对")
            backup = [_new_backup_code() for _ in range(BACKUP_CODE_COUNT)]
            user["backup_codes"] = [{"hash": _sha(_norm_backup(c)), "used": False} for c in backup]
            self._save(data)
        return {"ok": True, "backup_codes": backup}

    def _consume_totp(self, user: dict[str, Any], code: str) -> bool:
        """验证 6 位码或备用码；成功则就地标记（调用方负责保存）。"""
        raw = re.sub(r"\s+", "", str(code or ""))
        if re.fullmatch(r"\d{6}", raw):
            step = totp_verify(str(user.get("totp_secret") or ""), raw)
            if step is None or step <= int(user.get("totp_last_step") or 0):
                return False
            user["totp_last_step"] = step
            return True
        digest = _sha(_norm_backup(raw))
        for item in user.get("backup_codes") or []:
            if not item.get("used") and hmac.compare_digest(str(item.get("hash") or ""), digest):
                item["used"] = True
                return True
        return False

    def login_totp(self, ticket: str, code: str, request: Any) -> dict[str, Any]:
        digest = _sha(str(ticket or "").strip())
        with self._lock:
            data = self._load()
            self._prune(data)
            found = next(
                (t for t in data["tickets"]
                 if t.get("purpose") == "totp-login" and hmac.compare_digest(str(t.get("hash") or ""), digest)),
                None,
            )
            if found is None:
                raise AuthError("这个登录请求过期了，重新输一次密码")
            user = next((u for u in data["users"] if u.get("id") == found.get("user_id")), None)
            if user is None or not user.get("totp_enabled"):
                raise AuthError("账号状态变了，重新登录")
            if not self._consume_totp(user, code):
                found["tries"] = int(found.get("tries") or 0) + 1
                if found["tries"] >= 5:
                    data["tickets"] = [t for t in data["tickets"] if t is not found]
                self._save(data)
                raise AuthError("验证码不对" + ("（试太多次了，重新输密码）" if found["tries"] >= 5 else ""))
            data["tickets"] = [t for t in data["tickets"] if t is not found]
            self._save(data)
        token = self._issue_session(user, request)
        logger.info("账号 %s 通过了身份验证器", user.get("email"))
        return {"user": self.public_user(user), "token": token}

    def reset_with_totp(self, email_raw: str, code: str, new_password: str) -> dict[str, Any]:
        email = _valid_email(email_raw)
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("email") == email), None)
            if user is None or not user.get("totp_enabled"):
                raise AuthError("这个账号没有启用身份验证器")
            if not self._consume_totp(user, code):
                self._save(data)
                raise AuthError("验证码不对")
            check_password_strength(new_password, email)
            user["password"] = hash_password(new_password)
            new_code = _new_recovery_code()
            user["recovery_hash"] = _sha(_norm_code(new_code))
            user["failed"] = 0
            user["locked_until"] = 0
            uid = user["id"]
            data["sessions"] = [s for s in data["sessions"] if s.get("user_id") != uid]
            self._save(data)
        logger.info("账号 %s 用身份验证器重置了密码", email)
        return {"ok": True, "email": email, "recovery_code": new_code}

    # ---------------- API 密钥 ----------------
    def create_key(self, user_id: str, name: str) -> dict[str, Any]:
        raw = "ak_" + secrets.token_urlsafe(24)
        entry = {
            "id": new_id("key"),
            "name": str(name or "").strip()[:40] or "未命名密钥",
            "prefix": raw[:12],
            "hash": _sha(raw),
            "created": _now(),
            "last_used": "",
        }
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("id") == user_id), None)
            if user is None:
                raise AuthError("登录状态失效，重新登录")
            if user.get("guest"):
                raise ForbiddenError("游客账号不能新建 API 密钥：临时身份不保存密钥，注册正式账号才能用")
            user.setdefault("api_keys", []).append(entry)
            if len(user["api_keys"]) > 20:
                raise AppError("一个账号最多 20 个密钥，先删几个")
            self._save(data)
        logger.info("账号 %s 生成了 API 密钥 %s", user_id, entry["id"])
        return {"key": raw, "item": {k: v for k, v in entry.items() if k != "hash"}}

    def delete_key(self, user_id: str, key_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if u.get("id") == user_id), None)
            if user is None:
                raise AuthError("登录状态失效，重新登录")
            before = len(user.get("api_keys") or [])
            user["api_keys"] = [k for k in (user.get("api_keys") or []) if k.get("id") != key_id]
            if len(user["api_keys"]) == before:
                raise AppError("没有这个密钥")
            self._save(data)
        return {"ok": True}

    def devices(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load()
            self._prune(data)
            out = [
                {"id": s.get("id"), "created": s.get("created"), "ip": s.get("ip"), "ua": s.get("ua")}
                for s in data["sessions"] if s.get("user_id") == user_id
            ]
            self._save(data)
        return sorted(out, key=lambda x: str(x.get("created") or ""), reverse=True)

    # ---------------- Google 登录 / 身份认证 ----------------
    def public_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user.get("id"),
            "email": user.get("email"),
            "name": user.get("name"),
            "role": user.get("role") or "user",
            "has_password": bool(user.get("password")),
            "google": bool(user.get("google_sub")),
            "totp": bool(user.get("totp_enabled")),
            "backup_codes_left": sum(1 for h in (user.get("backup_codes") or []) if not h.get("used")),
            "google_email": user.get("google_email") or "",
            "created_at": user.get("created_at") or "",
            "last_login": user.get("last_login") or "",
            "guest": bool(user.get("guest")),
            "guest_expires": float(user.get("guest_expires") or 0),
            "guest_expires_at": _fmt_time(user.get("guest_expires")) if user.get("guest") else "",
            "guest_quota_mb": int(user.get("guest_quota_mb") or 0),
            "guest_unlocked": bool(user.get("guest_unlocked")),
            "guest_dir": user.get("id") if user.get("guest") else "",
            "keys": [{k: v for k, v in item.items() if k != "hash"} for item in (user.get("api_keys") or [])],
        }

    def me(self, user_id: str) -> dict[str, Any]:
        user = next((u for u in self.users() if u.get("id") == user_id), None)
        if user is None:
            raise AuthError("登录状态失效，重新登录")
        return self.public_user(user)

    def user_for_identity(self, ident: dict[str, Any]) -> dict[str, Any]:
        if ident.get("via") == "token":
            raise ForbiddenError("这是全局 API Token 通道，不能操作账号；请用账号登录或 API 密钥")
        user = next((u for u in self.users() if u.get("id") == ident.get("user_id")), None)
        if user is None:
            raise AuthError("登录状态失效，重新登录")
        return user

    def google_configure(self, body: dict[str, Any], ident: dict[str, Any]) -> dict[str, Any]:
        if ident.get("via") != "token":
            user = self.user_for_identity(ident)
            if (user or {}).get("role") != "owner":
                raise ForbiddenError("只有 owner 账号能改 Google 登录配置")
        with self._lock:
            data = self._load()
            cfg = data.setdefault("config", {})
            if "client_id" in body:
                cfg["google_client_id"] = str(body.get("client_id") or "").strip()
            if "client_secret" in body:
                cfg["google_client_secret"] = str(body.get("client_secret") or "").strip()
            if "allow_register" in body:
                cfg["allow_register"] = bool(body.get("allow_register"))
            if "redirect_base" in body:
                cfg["redirect_base"] = str(body.get("redirect_base") or "").strip().rstrip("/")
            self._save(data)
        return self.google_public()

    def google_public(self) -> dict[str, Any]:
        cfg = self.config()
        secret = str(cfg.get("google_client_secret") or "")
        return {
            "client_id": str(cfg.get("google_client_id") or ""),
            "has_secret": bool(secret),
            "secret_tail": secret[-4:] if len(secret) > 6 else "",
            "allow_register": bool(cfg.get("allow_register")),
            "redirect_base": str(cfg.get("redirect_base") or ""),
            "redirect_uri": self.redirect_uri(),
            "on": bool(cfg.get("google_client_id") and secret),
        }

    def redirect_uri(self) -> str:
        base = str(self.config().get("redirect_base") or os.getenv("AUTH_BASE_URL") or "").strip().rstrip("/")
        if not base:
            base = f"http://127.0.0.1:{self.settings.port}"
        return base + "/api/auth/google/callback"

    def google_start(self, purpose: str, email: str = "") -> str:
        cfg = self.config()
        if not (cfg.get("google_client_id") and cfg.get("google_client_secret")):
            raise AppError("还没配置 Google 登录：设置 → 账号与安全 → Google 登录，填 Client ID / Secret")
        purpose = "reset" if str(purpose).strip().lower() == "reset" else "login"
        state = secrets.token_urlsafe(24)
        with self._lock:
            data = self._load()
            self._prune(data)
            data["states"].append({
                "state": state, "purpose": purpose, "email": str(email or "").strip().lower(),
                "expires": time.time() + STATE_SECONDS,
            })
            self._save(data)
        params = {
            "client_id": cfg["google_client_id"],
            "redirect_uri": self.redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        if email:
            params["login_hint"] = email
        from urllib.parse import quote

        return GOOGLE_AUTH_URL + "?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())

    def google_callback(self, code: str, state: str) -> dict[str, Any]:
        if not code or not state:
            raise AppError("Google 回调缺少参数（code/state）")
        with self._lock:
            data = self._load()
            self._prune(data)
            found = next((s for s in data["states"] if s.get("state") == state), None)
            if found is None:
                raise AuthError("这个登录请求已过期，重新点一次 Google 登录")
            data["states"] = [s for s in data["states"] if s is not found]
            self._save(data)
        info = self._google_exchange(code)
        email = str(info.get("email") or "").strip().lower()
        sub = str(info.get("sub") or "")
        if not email or not sub:
            raise AuthError("Google 没返回邮箱，换一个账号试试")
        if info.get("email_verified") in (False, "false", 0):
            raise AuthError("这个 Google 邮箱还没验证")

        if found.get("purpose") == "reset":
            user = next((u for u in self.users() if u.get("google_sub") == sub or u.get("email") == email), None)
            if user is None:
                raise AuthError("这个 Google 账号没有绑定任何本机账号")
            ticket = secrets.token_urlsafe(24)
            with self._lock:
                data = self._load()
                self._prune(data)
                data["tickets"].append({
                    "hash": _sha(ticket), "user_id": user["id"], "purpose": "reset",
                    "expires": time.time() + TICKET_SECONDS,
                })
                self._save(data)
            logger.info("Google 身份认证通过，给 %s 发重置票", email)
            from urllib.parse import quote

            return {
                "ok": True,
                "reset_ticket": ticket,
                "redirect": f"/login?reset_ticket={quote(ticket)}&email={quote(email)}",
            }

        user = next((u for u in self.users() if u.get("google_sub") == sub), None)
        if user is None:
            same_email = next((u for u in self.users() if u.get("email") == email), None)
            if same_email is not None:
                with self._lock:
                    data = self._load()
                    for item in data["users"]:
                        if item.get("id") == same_email["id"]:
                            item["google_sub"] = sub
                            item["google_email"] = email
                            user = item
                    self._save(data)
        if user is None:
            if not self.register_open():
                raise AuthError(f"这台设备没有 {email} 的账号，且注册已关闭")
            user = self._create_google_user(email, sub, str(info.get("name") or ""))
        token = self._issue_session(user, _NullRequest())
        return {"ok": True, "token": token, "redirect": "/", "user": self.public_user(user)}

    def _create_google_user(self, email: str, sub: str, name: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            recovery = _new_recovery_code()
            user = {
                "id": new_id("usr"),
                "email": email,
                "name": (name.strip()[:40] or email.split("@")[0]),
                "role": "owner" if not [u for u in data["users"] if not u.get("guest")] else "user",
                "password": None,
                "google_sub": sub,
                "google_email": email,
                "totp_secret": "",
                "totp_enabled": False,
                "totp_last_step": 0,
                "totp_pending": "",
                "backup_codes": [],
                "recovery_hash": _sha(_norm_code(recovery)),
                "api_keys": [], "created_at": _now(), "last_login": "",
                "failed": 0, "locked_until": 0,
            }
            data["users"].append(user)
            self._save(data)
        logger.info("Google 登录自动创建账号：%s", email)
        return user

    def _google_exchange(self, code: str) -> dict[str, Any]:
        cfg = self.config()
        try:
            token = _post_form(GOOGLE_TOKEN_URL, {
                "code": code,
                "client_id": cfg.get("google_client_id"),
                "client_secret": cfg.get("google_client_secret"),
                "redirect_uri": self.redirect_uri(),
                "grant_type": "authorization_code",
            })
        except NetError as exc:
            raise AppError(f"跟 Google 换 token 失败：{exc}") from None
        access = str(token.get("access_token") or "")
        if not access:
            raise AppError(f"Google 没给 access_token：{str(token)[:200]}")
        try:
            info = json_request("GET", GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
        except NetError as exc:
            raise AppError(f"拿 Google 用户信息失败：{exc}") from None
        return info



# ---------------- TOTP（Google Authenticator 那套） ----------------
TOTP_STEP = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # 前后各容忍 30 秒，手机时钟差一点也能进
BACKUP_CODE_COUNT = 10


def new_totp_secret() -> str:
    """20 字节随机 → base32（32 个字符，Google Authenticator 直接能吃）。"""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret: str, counter: int, digits: int = TOTP_DIGITS) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded.upper(), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_code(secret: str, at: float | None = None, step: int = TOTP_STEP) -> str:
    counter = int((at if at is not None else time.time()) // step)
    return _hotp(secret, counter)


def totp_next_code(secret: str, at: float | None = None) -> str:
    """下一个 30 秒窗口的码：测试与「刚用过要等下一格」的场景用。"""
    counter = int((at if at is not None else time.time()) // TOTP_STEP) + 1
    return _hotp(secret, counter)


def totp_verify(secret: str, code: str, at: float | None = None) -> int | None:
    """对得上就返回用掉的时间步（用于防重放），对不上返回 None。"""
    code = re.sub(r"\s+", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code):
        return None
    now = at if at is not None else time.time()
    current = int(now // TOTP_STEP)
    for delta in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        counter = current + delta
        if hmac.compare_digest(_hotp(secret, counter), code):
            return counter
    return None


def otpauth_uri(secret: str, email: str, issuer: str = "Agent") -> str:
    label = f"{issuer}:{email}"
    params = {
        "secret": secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": str(TOTP_DIGITS),
        "period": str(TOTP_STEP),
    }
    from urllib.parse import quote

    return "otpauth://totp/" + quote(label, safe="") + "?" + "&".join(
        f"{k}={quote(str(v), safe='')}" for k, v in params.items()
    )


def _new_backup_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉了 I/O/0/1
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return raw[:4] + "-" + raw[4:]


def _norm_backup(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(code or "")).upper()


class _NullRequest:
    """Google 回调里没有原始请求对象，用个空壳满足 _issue_session。"""

    def header(self, name: str, default: str | None = None) -> str | None:
        return default


def _post_form(url: str, fields: dict[str, Any]) -> dict[str, Any]:
    """POST 表单（OAuth token 端点要 application/x-www-form-urlencoded）。"""
    import urllib.error
    import urllib.request
    from urllib.parse import urlencode

    body = urlencode({k: v for k, v in fields.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        snippet = ""
        try:
            snippet = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        raise NetError(f"HTTP {exc.code}：{snippet or exc.reason}", status=exc.code, body=snippet) from exc
    except Exception as exc:  # noqa: BLE001
        raise NetError(f"连不上：{exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw[:2000]}
    return data if isinstance(data, dict) else {"_list": data}


def cookie_for(token: str) -> str:
    return _cookie_header(token)


def clear_cookie() -> str:
    return _clear_cookie_header()
