"""商家版订阅：套餐 / 兑换码 / 每账号积分与有效期。

计费口径：1 积分 = 1000 token，按每次对话累计 token 向上取整，最低扣 1 分。
免费版永久有效，每月重置 400 积分；付费套餐按积分给额度，商家可自己改。
兑换码仍然保留：没有商户号也能先卖卡；有商户号之后再把「支付成功 → 发码」
接到同一个 redeem() 上，业务逻辑不用改。
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .deploy import is_merchant
from .errors import AppError

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TOKENS_PER_CREDIT = 1000
FREE_PLAN_ID = "free"
DEFAULT_PLANS = [
    {
        "id": "free", "name": "免费版", "days": 0, "credits": 400, "price_cents": 0,
        "storage_mb": 1024, "monthly_reset": True,
        "note": "永久有效，每月重置 400 积分；1 积分 = 1000 token",
    },
    {
        "id": "start", "name": "入门月卡", "days": 30, "credits": 1000, "price_cents": 990,
        "storage_mb": 2048, "monthly_reset": False, "note": "适合自己带 Key 的轻量用户",
    },
    {
        "id": "pro", "name": "进阶季卡", "days": 90, "credits": 6000, "price_cents": 2690,
        "storage_mb": 5120, "monthly_reset": False, "note": "6000 积分，更长有效期 + 更大空间",
    },
]


class SubscriptionRequired(AppError):
    status_code = 402
    code = "subscription_required"


class SubscriptionQuota(AppError):
    status_code = 429
    code = "subscription_quota"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fmt_time(value: Any) -> str:
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts > 0 else ""


def _month_key(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m")


def _new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return "AG-" + raw[:4] + "-" + raw[4:]


def _norm_code(raw: str) -> str:
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum() or ch == "-")


def _code_id(entry: dict[str, Any]) -> str:
    return str(entry.get("id") or str(entry.get("hash") or "")[:10])


def _credits(plan: dict[str, Any]) -> int:
    """兼容旧套餐：没有 credits 就沿用原来的 messages。"""
    return max(0, int(plan.get("credits") if plan.get("credits") is not None else plan.get("messages") or 0))


def _permanent(plan: dict[str, Any]) -> bool:
    return plan.get("id") == FREE_PLAN_ID or int(plan.get("days") or 0) <= 0


class BillingService:
    def __init__(self, data_dir: Path) -> None:
        self._file = Path(data_dir) / "billing.json"
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None

    def enabled(self) -> bool:
        return is_merchant()

    def _load(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("plans", [])
        data.setdefault("codes", [])
        data.setdefault("accounts", {})
        data.setdefault("settings", {"codes_enabled": False})

        # 旧 billing.json 只有 messages；补成积分。免费版缺了就补上，保证注册就有 400 分。
        plans = data["plans"]
        existing = {str(p.get("id") or "") for p in plans if isinstance(p, dict)}
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            if plan.get("credits") is None:
                plan["credits"] = int(plan.get("messages") or 0)
            # 旧版默认套餐迁移：start 300 条 → 1000 分，pro 1500 条 → 6000 分。
            if plan.get("id") == "start" and int(plan.get("credits") or 0) == 300:
                plan["credits"] = 1000
                plan["storage_mb"] = 2048
            if plan.get("id") == "pro" and int(plan.get("credits") or 0) == 1500:
                plan["credits"] = 6000
                plan["storage_mb"] = 5120
            plan.setdefault("monthly_reset", plan.get("id") == FREE_PLAN_ID)
            plan.setdefault("storage_mb", 1024)
            plan.setdefault("days", 0 if plan.get("id") == FREE_PLAN_ID else 30)
        for default in DEFAULT_PLANS:
            if default["id"] not in existing:
                plans.append(dict(default))
        plans[:] = [p for p in plans if isinstance(p, dict)]
        plans.sort(key=lambda p: 0 if p.get("id") == FREE_PLAN_ID else 1)

        # 旧账号只有 remaining_messages；先一次性迁移成积分，避免老用户被当成 0 分。
        for account in data["accounts"].values():
            if not isinstance(account, dict):
                continue
            if account.get("remaining_credits") is None:
                account["remaining_credits"] = int(account.get("remaining_messages") or 0)
            if account.get("used_credits") is None:
                account["used_credits"] = int(account.get("used_messages") or 0)
        self._cache = data
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._file)
        self._cache = data

    def plans(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(p) for p in self._load().get("plans", [])]

    def upsert_plan(self, body: dict[str, Any]) -> dict[str, Any]:
        pid = str(body.get("id") or "").strip().lower().replace(" ", "-")[:24]
        if not pid:
            raise AppError("套餐 id 不能为空")
        name = str(body.get("name") or pid)[:40]
        try:
            default_days = 0 if pid == FREE_PLAN_ID else 30
            days = max(0, int(body.get("days") if body.get("days") not in (None, "") else default_days))
            credits = max(0, int(body.get("credits") if body.get("credits") not in (None, "") else body.get("messages") or 300))
            price = max(0, int(body.get("price_cents") or 0))
            storage = max(1, int(body.get("storage_mb") or 1024))
        except (TypeError, ValueError) as exc:
            raise AppError(f"套餐参数不对：{exc}") from exc
        plan = {
            "id": pid, "name": name, "days": days, "credits": credits, "price_cents": price,
            "storage_mb": storage, "monthly_reset": bool(body.get("monthly_reset")) or pid == FREE_PLAN_ID,
            "note": str(body.get("note") or "")[:100],
        }
        with self._lock:
            data = self._load()
            plans = data.setdefault("plans", [])
            for index, item in enumerate(plans):
                if item.get("id") == pid:
                    plans[index] = plan
                    break
            else:
                plans.append(plan)
            # 免费版始终排在最前面，会员一注册就能看到。
            plans.sort(key=lambda p: 0 if p.get("id") == FREE_PLAN_ID else 1)
            self._save(data)
        return plan

    def generate_codes(self, plan_id: str, count: int, note: str = "") -> list[str]:
        plan = next((p for p in self.plans() if p.get("id") == plan_id), None)
        if plan is None:
            raise AppError(f"没有这个套餐：{plan_id}")
        if not self.codes_enabled():
            raise AppError("兑换码还没开启：先在商家后台确认开启，再生成")
        count = max(1, min(int(count or 1), 500))
        raw_codes: list[str] = []
        with self._lock:
            data = self._load()
            codes = data.setdefault("codes", [])
            for _ in range(count):
                code = _new_code()
                while any(c.get("hash") == _sha(code) for c in codes):
                    code = _new_code()
                raw_codes.append(code)
                digest = _sha(code)
                codes.append({
                    "id": digest[:10], "hash": digest, "prefix": code[:7] + "…", "plan_id": plan_id,
                    "created": time.time(), "used_by": "", "used_at": 0, "revoked": False,
                    "note": str(note or "")[:100],
                })
            self._save(data)
        return raw_codes

    def list_codes(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            codes = list(self._load().get("codes", []))
        codes.sort(key=lambda c: float(c.get("created") or 0), reverse=True)
        out = []
        for item in codes[:max(1, min(limit, 200))]:
            out.append({
                "id": _code_id(item), "prefix": item.get("prefix"), "plan_id": item.get("plan_id"),
                "created": _fmt_time(item.get("created")),
                "used": bool(item.get("used_by")), "used_by": item.get("used_by") or "",
                "used_at": _fmt_time(item.get("used_at")), "revoked": bool(item.get("revoked")),
                "note": item.get("note") or "",
            })
        return out

    def reset_account(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            accounts = data.setdefault("accounts", {})
            existed = accounts.pop(str(user_id), None) is not None
            self._save(data)
        return {"user_id": str(user_id), "cleared": existed}

    def revoke_code(self, code_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            entry = next((c for c in data.get("codes", []) if _code_id(c) == str(code_id)), None)
            if entry is None:
                raise AppError("没有这个兑换码")
            entry["revoked"] = True
            entry["revoked_at"] = time.time()
            self._save(data)
        return {"id": code_id, "revoked": True}

    def reset_code(self, code_id: str, revoke_account: bool = True) -> dict[str, Any]:
        """把已用/作废的码改回未使用；默认同时收回兑换人整份订阅，避免一码两次订阅。"""
        with self._lock:
            data = self._load()
            entry = next((c for c in data.get("codes", []) if _code_id(c) == str(code_id)), None)
            if entry is None:
                raise AppError("没有这个兑换码")
            used_by = str(entry.get("used_by") or "")
            if used_by and not revoke_account:
                raise AppError("这张码已经被用过：要放回未使用就必须同时收回使用者的订阅，不能只重置码")
            cleared = False
            if used_by and revoke_account:
                accounts = data.setdefault("accounts", {})
                if used_by in accounts:
                    accounts.pop(used_by, None)
                    cleared = True
            entry.update({"used_by": "", "used_at": 0, "revoked": False})
            self._save(data)
        return {"id": code_id, "account_cleared": cleared, "used_by": used_by}

    def _plan(self, data: dict[str, Any], plan_id: str) -> dict[str, Any] | None:
        return next((p for p in data.get("plans", []) if p.get("id") == plan_id), None)

    def _free_account(self, user_id: str, plan: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        return {
            "user_id": str(user_id),
            "plan_id": FREE_PLAN_ID,
            "plan_name": str(plan.get("name") or "免费版"),
            "expires": 0,
            "remaining_credits": _credits(plan),
            "used_credits": 0,
            "tokens_used": 0,
            "storage_mb": int(plan.get("storage_mb") or 1024),
            "credit_period": _month_key(now),
            "updated": now,
        }

    def ensure_free(self, user_id: str) -> dict[str, Any]:
        """正式会员没有付费订阅时，自动给免费版；已过期的付费订阅也回落到免费版。"""
        if not self.enabled() or not str(user_id or ""):
            return {}
        now = time.time()
        with self._lock:
            data = self._load()
            free_plan = self._plan(data, FREE_PLAN_ID)
            if free_plan is None:
                return {}
            accounts = data.setdefault("accounts", {})
            account = dict(accounts.get(str(user_id)) or {})
            plan = self._plan(data, str(account.get("plan_id") or "")) or {}
            expired_paid = (
                account and plan.get("id") != FREE_PLAN_ID
                and float(account.get("expires") or 0) > 0
                and float(account.get("expires") or 0) <= now
            )
            if not account or not plan or expired_paid:
                account = self._free_account(str(user_id), free_plan, now)
                accounts[str(user_id)] = account
                self._save(data)
                return dict(account)
            if plan.get("id") == FREE_PLAN_ID:
                changed = False
                if _month_key(now) != str(account.get("credit_period") or ""):
                    account["remaining_credits"] = _credits(free_plan)
                    account["used_credits"] = 0
                    account["credit_period"] = _month_key(now)
                    changed = True
                if account.get("remaining_credits") is None:
                    account["remaining_credits"] = _credits(free_plan)
                    changed = True
                account["storage_mb"] = max(int(account.get("storage_mb") or 0), int(free_plan.get("storage_mb") or 1024))
                if changed:
                    accounts[str(user_id)] = account
                    self._save(data)
        return dict(account) if account else {}

    def account(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            account = dict(data.get("accounts", {}).get(str(user_id)) or {})
            plan = self._plan(data, str(account.get("plan_id") or "")) or {}
            now = time.time()

            # 旧数据 remaining_messages → remaining_credits
            if account and account.get("remaining_credits") is None:
                account["remaining_credits"] = int(account.get("remaining_messages") or 0)
            if account and account.get("used_credits") is None:
                account["used_credits"] = int(account.get("used_messages") or 0)

            # 免费版每月重置：把剩余积分重置回 400，不累计。
            if account and plan.get("id") == FREE_PLAN_ID and plan.get("monthly_reset"):
                if _month_key(now) != str(account.get("credit_period") or ""):
                    account["remaining_credits"] = _credits(plan)
                    account["used_credits"] = 0
                    account["credit_period"] = _month_key(now)
                    account["updated"] = now
                    accounts = data.setdefault("accounts", {})
                    accounts[str(user_id)] = account
                    self._save(data)

            expires = float(account.get("expires") or 0)
            permanent = bool(account) and (plan.get("id") == FREE_PLAN_ID or expires <= 0)
            active = bool(account) and (permanent or expires > now)
            remaining = int(account.get("remaining_credits") or 0)
            return {
                "enabled": self.enabled(),
                "plan_id": account.get("plan_id") or "",
                "plan_name": account.get("plan_name") or (plan.get("name") or ""),
                "status": "active" if active else ("expired" if account else "none"),
                "expires": expires,
                "expires_at": _fmt_time(expires),
                "permanent": permanent,
                "remaining_credits": remaining,
                "used_credits": int(account.get("used_credits") or 0),
                "tokens_used": int(account.get("tokens_used") or 0),
                "storage_mb": int(account.get("storage_mb") or plan.get("storage_mb") or 0),
                "credit_period": account.get("credit_period") or "",
                "monthly_reset": bool(plan.get("monthly_reset")),
                "credits_per_month": _credits(plan) if plan.get("monthly_reset") else 0,
                "tokens_per_credit": TOKENS_PER_CREDIT,
                # 兼容旧前端/旧接口字段：1 条 ≈ 1 分，只读展示用
                "remaining_messages": remaining,
                "used_messages": int(account.get("used_credits") or 0),
            }

    def require(self, user_id: str) -> dict[str, Any]:
        if not self.enabled():
            return {}
        self.ensure_free(user_id)
        account = self.account(user_id)
        if account["status"] == "none":
            raise SubscriptionRequired("还没有订阅：在「账号与安全 → 订阅」输入兑换码开通")
        if account["status"] == "expired":
            raise SubscriptionRequired("订阅已到期：续一张兑换码就能继续用，数据不会删")
        if account["remaining_credits"] <= 0:
            raise SubscriptionQuota(
                f"本月积分用完了（1 积分 = {TOKENS_PER_CREDIT} token）：免费版每月重置 {account.get('credits_per_month') or 400} 分，"
                "也可以输入兑换码升级"
            )
        return account

    def codes_enabled(self) -> bool:
        return bool(self._load().get("settings", {}).get("codes_enabled"))

    def set_codes_enabled(self, value: bool) -> bool:
        with self._lock:
            data = self._load()
            data.setdefault("settings", {})["codes_enabled"] = bool(value)
            self._save(data)
        return bool(value)

    def reset(self) -> None:
        with self._lock:
            self._cache = None
            try:
                self._file.unlink()
            except OSError:
                pass

    def redeem(self, user_id: str, raw_code: str) -> dict[str, Any]:
        code = _norm_code(raw_code)
        if not code:
            raise AppError("兑换码不能为空")
        if not self.codes_enabled():
            raise AppError("商家还没开启兑换码：让管理员在「商家后台」里确认开启")
        now = time.time()
        with self._lock:
            data = self._load()
            entry = next((c for c in data.get("codes", []) if c.get("hash") == _sha(code)), None)
            if entry is None:
                raise AppError("兑换码不存在")
            if entry.get("revoked"):
                raise AppError("这个兑换码已作废：找管理员换一张")
            if entry.get("used_by"):
                raise AppError("这个兑换码已经被用过了")
            plan = self._plan(data, str(entry.get("plan_id") or ""))
            if plan is None:
                raise AppError("兑换码对应的套餐已经下架")
            accounts = data.setdefault("accounts", {})
            account = dict(accounts.get(str(user_id)) or {})
            old_plan = self._plan(data, str(account.get("plan_id") or "")) or {}
            # 从免费版买付费套餐：付费额度从 0 开始算，不把免费额度叠进去。
            base_credits = 0 if old_plan.get("id") == FREE_PLAN_ID else int(account.get("remaining_credits") or 0)
            if _permanent(plan):
                expires = 0
            else:
                base = max(now, float(account.get("expires") or 0)) if old_plan.get("id") != FREE_PLAN_ID else now
                expires = base + int(plan.get("days") or 0) * 86400
            account.update({
                "user_id": str(user_id),
                "plan_id": plan["id"],
                "plan_name": plan["name"],
                "expires": expires,
                "remaining_credits": base_credits + _credits(plan),
                "used_credits": int(account.get("used_credits") or 0),
                "tokens_used": int(account.get("tokens_used") or 0),
                "storage_mb": max(
                    int(account.get("storage_mb") or 0) if old_plan.get("id") != FREE_PLAN_ID else 0,
                    int(plan.get("storage_mb") or 0),
                ),
                "credit_period": _month_key(now) if plan.get("monthly_reset") else "",
                "updated": now,
            })
            accounts[str(user_id)] = account
            entry["used_by"] = str(user_id)
            entry["used_at"] = now
            self._save(data)
        return self.account(user_id)

    def consume(self, user_id: str, tokens: int = 0, messages: int = 1) -> dict[str, Any]:
        """扣积分：1 积分 = 1000 token，向上取整，最低 1 分。"""
        if not self.enabled():
            return {}
        with self._lock:
            data = self._load()
            accounts = data.setdefault("accounts", {})
            account = dict(accounts.get(str(user_id)) or {})
            if not account:
                return self.account(user_id)
            tokens = max(0, int(tokens or 0))
            cost = max(1, int(math.ceil(tokens / TOKENS_PER_CREDIT))) if tokens > 0 else 1
            plan = self._plan(data, str(account.get("plan_id") or "")) or {}
            if plan.get("id") == FREE_PLAN_ID and _month_key() != str(account.get("credit_period") or ""):
                account["remaining_credits"] = _credits(plan)
                account["used_credits"] = 0
                account["credit_period"] = _month_key()
            account["remaining_credits"] = max(0, int(account.get("remaining_credits") or 0) - cost)
            account["used_credits"] = int(account.get("used_credits") or 0) + cost
            account["tokens_used"] = int(account.get("tokens_used") or 0) + tokens
            accounts[str(user_id)] = account
            self._save(data)
        return self.account(user_id)

    def grant(
        self, user_id: str, plan_id: str, days: int | None = None,
        credits: int | None = None, messages: int | None = None,
    ) -> dict[str, Any]:
        plan = next((p for p in self.plans() if p.get("id") == plan_id), None)
        if plan is None:
            raise AppError(f"没有这个套餐：{plan_id}")
        now = time.time()
        add_days = int(days if days is not None else plan.get("days") or 0)
        add_credits = int(credits if credits is not None else (messages if messages is not None else _credits(plan)))
        with self._lock:
            data = self._load()
            accounts = data.setdefault("accounts", {})
            account = dict(accounts.get(str(user_id)) or {})
            old_plan = self._plan(data, str(account.get("plan_id") or "")) or {}
            base_credits = 0 if old_plan.get("id") == FREE_PLAN_ID else int(account.get("remaining_credits") or 0)
            if _permanent(plan):
                expires = 0
            else:
                base = max(now, float(account.get("expires") or 0)) if old_plan.get("id") != FREE_PLAN_ID else now
                expires = base + max(1, add_days) * 86400
            account.update({
                "user_id": str(user_id),
                "plan_id": plan["id"],
                "plan_name": plan["name"],
                "expires": expires,
                "remaining_credits": base_credits + max(0, add_credits),
                "used_credits": int(account.get("used_credits") or 0),
                "tokens_used": int(account.get("tokens_used") or 0),
                "storage_mb": max(
                    int(account.get("storage_mb") or 0) if old_plan.get("id") != FREE_PLAN_ID else 0,
                    int(plan.get("storage_mb") or 0),
                ),
                "credit_period": _month_key(now) if plan.get("monthly_reset") else "",
                "updated": now,
            })
            accounts[str(user_id)] = account
            self._save(data)
        return self.account(user_id)
