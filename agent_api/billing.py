"""商家版订阅：套餐 / 兑换码 / 每账号额度与有效期。

为什么用兑换码而不是直接接支付宝：没有商户号也能先卖卡；有商户号之后再把
「支付成功 → 发码」接到同一个 redeem() 上，业务逻辑不用改。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .deploy import is_merchant
from .errors import AppError

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULT_PLANS = [
    {"id": "start", "name": "入门月卡", "days": 30, "messages": 300, "price_cents": 990, "storage_mb": 1024, "note": "适合自己带 Key 的轻量用户"},
    {"id": "pro", "name": "进阶季卡", "days": 90, "messages": 1500, "price_cents": 2690, "storage_mb": 3072, "note": "更长有效期 + 更大空间"},
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


def _new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return "AG-" + raw[:4] + "-" + raw[4:]


def _norm_code(raw: str) -> str:
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum() or ch == "-")


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
        data.setdefault("plans", [dict(p) for p in DEFAULT_PLANS])
        data.setdefault("codes", [])
        data.setdefault("accounts", {})
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
            days = max(1, int(body.get("days") or 30))
            messages = max(0, int(body.get("messages") or 300))
            price = max(0, int(body.get("price_cents") or 0))
            storage = max(1, int(body.get("storage_mb") or 1024))
        except (TypeError, ValueError) as exc:
            raise AppError(f"套餐参数不对：{exc}") from exc
        plan = {"id": pid, "name": name, "days": days, "messages": messages, "price_cents": price,
                "storage_mb": storage, "note": str(body.get("note") or "")[:100]}
        with self._lock:
            data = self._load()
            plans = data.setdefault("plans", [])
            for index, item in enumerate(plans):
                if item.get("id") == pid:
                    plans[index] = plan
                    break
            else:
                plans.append(plan)
            self._save(data)
        return plan

    def generate_codes(self, plan_id: str, count: int, note: str = "") -> list[str]:
        plan = next((p for p in self.plans() if p.get("id") == plan_id), None)
        if plan is None:
            raise AppError(f"没有这个套餐：{plan_id}")
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
                codes.append({
                    "hash": _sha(code), "prefix": code[:7] + "…", "plan_id": plan_id,
                    "created": time.time(), "used_by": "", "used_at": 0,
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
                "prefix": item.get("prefix"), "plan_id": item.get("plan_id"),
                "created": _fmt_time(item.get("created")),
                "used": bool(item.get("used_by")), "used_by": item.get("used_by") or "",
                "used_at": _fmt_time(item.get("used_at")), "note": item.get("note") or "",
            })
        return out

    def _plan(self, data: dict[str, Any], plan_id: str) -> dict[str, Any] | None:
        return next((p for p in data.get("plans", []) if p.get("id") == plan_id), None)

    def account(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            account = dict(data.get("accounts", {}).get(str(user_id)) or {})
            plan = self._plan(data, str(account.get("plan_id") or "")) or {}
        now = time.time()
        expires = float(account.get("expires") or 0)
        active = bool(account) and expires > now
        return {
            "enabled": self.enabled(),
            "plan_id": account.get("plan_id") or "",
            "plan_name": account.get("plan_name") or (plan.get("name") or ""),
            "status": "active" if active else ("expired" if account else "none"),
            "expires": expires,
            "expires_at": _fmt_time(expires),
            "remaining_messages": int(account.get("remaining_messages") or 0),
            "used_messages": int(account.get("used_messages") or 0),
            "tokens_used": int(account.get("tokens_used") or 0),
            "storage_mb": int(account.get("storage_mb") or plan.get("storage_mb") or 0),
        }

    def require(self, user_id: str) -> dict[str, Any]:
        if not self.enabled():
            return {}
        account = self.account(user_id)
        if account["status"] == "none":
            raise SubscriptionRequired("还没有订阅：在「账号与安全 → 订阅」输入兑换码开通")
        if account["status"] == "expired":
            raise SubscriptionRequired("订阅已到期：续一张兑换码就能继续用，数据不会删")
        if account["remaining_messages"] <= 0:
            raise SubscriptionQuota("这个订阅周期的消息额度用完了：续费后额度会累加")
        return account

    def redeem(self, user_id: str, raw_code: str) -> dict[str, Any]:
        code = _norm_code(raw_code)
        if not code:
            raise AppError("兑换码不能为空")
        now = time.time()
        with self._lock:
            data = self._load()
            entry = next((c for c in data.get("codes", []) if c.get("hash") == _sha(code)), None)
            if entry is None:
                raise AppError("兑换码不存在")
            if entry.get("used_by"):
                raise AppError("这个兑换码已经被用过了")
            plan = self._plan(data, str(entry.get("plan_id") or ""))
            if plan is None:
                raise AppError("兑换码对应的套餐已经下架")
            accounts = data.setdefault("accounts", {})
            account = dict(accounts.get(str(user_id)) or {})
            base = max(now, float(account.get("expires") or 0))
            account.update({
                "user_id": str(user_id),
                "plan_id": plan["id"],
                "plan_name": plan["name"],
                "expires": base + int(plan["days"]) * 86400,
                "remaining_messages": int(account.get("remaining_messages") or 0) + int(plan["messages"]),
                "used_messages": int(account.get("used_messages") or 0),
                "storage_mb": max(int(account.get("storage_mb") or 0), int(plan["storage_mb"])),
                "updated": now,
            })
            accounts[str(user_id)] = account
            entry["used_by"] = str(user_id)
            entry["used_at"] = now
            self._save(data)
        return self.account(user_id)

    def consume(self, user_id: str, tokens: int = 0, messages: int = 1) -> dict[str, Any]:
        if not self.enabled():
            return {}
        with self._lock:
            data = self._load()
            accounts = data.setdefault("accounts", {})
            account = dict(accounts.get(str(user_id)) or {})
            if not account:
                return self.account(user_id)
            account["remaining_messages"] = max(0, int(account.get("remaining_messages") or 0) - max(0, messages))
            account["used_messages"] = int(account.get("used_messages") or 0) + max(0, messages)
            account["tokens_used"] = int(account.get("tokens_used") or 0) + max(0, int(tokens))
            accounts[str(user_id)] = account
            self._save(data)
        return self.account(user_id)

    def grant(self, user_id: str, plan_id: str, days: int | None = None, messages: int | None = None) -> dict[str, Any]:
        plan = next((p for p in self.plans() if p.get("id") == plan_id), None)
        if plan is None:
            raise AppError(f"没有这个套餐：{plan_id}")
        now = time.time()
        add_days = int(days if days is not None else plan["days"])
        add_messages = int(messages if messages is not None else plan["messages"])
        with self._lock:
            data = self._load()
            accounts = data.setdefault("accounts", {})
            account = dict(accounts.get(str(user_id)) or {})
            base = max(now, float(account.get("expires") or 0))
            account.update({
                "user_id": str(user_id),
                "plan_id": plan["id"],
                "plan_name": plan["name"],
                "expires": base + max(1, add_days) * 86400,
                "remaining_messages": int(account.get("remaining_messages") or 0) + max(0, add_messages),
                "used_messages": int(account.get("used_messages") or 0),
                "storage_mb": max(int(account.get("storage_mb") or 0), int(plan["storage_mb"])),
                "updated": now,
            })
            accounts[str(user_id)] = account
            self._save(data)
        return self.account(user_id)
