"""定时任务：cron 表达式 + 到点自动跑一句话 / 跑一个工作流。

设计上的几个硬规矩：

- 只用标准库，所以 cron 解析自己写（5 字段：分 时 日 月 周）。支持 `*`、
  `*/n`、`a-b`、`a-b/n`、逗号列表、月份与星期的英文缩写，外加 @hourly /
  @daily / @weekly / @monthly / @every 10m 这几种口头写法。
- 日 和 周 都写了的时候按 Vixie cron 的老规矩：**任一命中即可**（
  `0 9 1 * 1` = 每月 1 号或每周一，不是「1 号且是周一」）。
- 任务跑在后台线程里，不阻塞任何请求；同一个任务不会叠着跑（上一次没完就跳过）。
- 每次运行都落到一个 `[定时] 名字` 的会话里，历史能翻；结果、耗时、错误都记在
  data/schedules.json 里，网页和终端都能看到。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import AppError, NotFoundError
from .utils import new_id

logger = logging.getLogger("agent.schedules")

_TICK_SECONDS = 20
_RUNNING: dict[str, bool] = {}
_RUNNING_LOCK = threading.Lock()

# 到点时最多同时跑几个。手机上一个任务跑完可能要好几分钟，同一分钟撞上 5 个
# 任务会一起打模型、互相抢 CPU；这里限制住，多的排队等。
_MAX_CONCURRENT = max(1, int(os.environ.get("SCHEDULE_CONCURRENCY", "2")))
_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT)

# 找「下一次」最多往后扫多少天。闰日（2/29）最远要等 4 年，所以给足 8 年；
# 扫到 8 年还没有，才认定为「永远不会触发」。
_MAX_SCAN_DAYS = 366 * 8

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

_SHORTCUTS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

_EVERY_RE = re.compile(r"^@every\s+(\d+)\s*(m|min|minute|minutes|h|hour|hours)$", re.I)


def normalize_cron(expr: str) -> str:
    """把口头写法归一成 5 字段 cron；非法就抛 AppError（带人话原因）。"""
    raw = str(expr or "").strip().lower()
    if not raw:
        raise AppError("cron 不能为空，例如：0 9 * * *（每天 9 点）")
    if raw in _SHORTCUTS:
        return _SHORTCUTS[raw]
    every = _EVERY_RE.match(raw)
    if every:
        amount = max(1, int(every.group(1)))
        unit = every.group(2).lower()
        if unit.startswith("h"):
            if amount > 23:
                raise AppError("@every 的小时数最多 23；更长的周期请用 cron 写具体时间")
            return f"0 */{amount} * * *"
        if amount > 59:
            raise AppError("@every 的分钟数最多 59；更长的周期请用 cron 写具体时间")
        return f"*/{amount} * * * *"
    fields = raw.split()
    if len(fields) != 5:
        raise AppError(f"cron 要 5 段（分 时 日 月 周），现在 {len(fields)} 段：{raw}")
    return " ".join(fields)


def _expand(expr: str, lo: int, hi: int, names: dict[str, int] | None = None) -> tuple[set[int], bool]:
    """展开一段字段，返回 (取值集合, 是否通配)。"""
    items: set[int] = set()
    text = str(expr or "").strip()
    wildcard = text in {"*", "?"} or text.startswith("*/")
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            raise AppError(f"cron 里有空的取值：{text}")
        step = 1
        if "/" in piece:
            piece, _, step_text = piece.partition("/")
            try:
                step = int(step_text)
            except ValueError:
                raise AppError(f"cron 步长不是数字：{text}") from None
            if step <= 0:
                raise AppError(f"cron 步长必须大于 0：{text}")
        piece = piece.strip() or "*"
        if piece in {"*", "?"}:
            start, stop = lo, hi
        elif "-" in piece.lstrip("-"):
            left, _, right = piece.partition("-")
            start, stop = _num(left, lo, hi, names), _num(right, lo, hi, names)
            if start > stop:
                start, stop = stop, start
        else:
            start = _num(piece, lo, hi, names)
            stop = hi if step > 1 else start
        items.update(range(start, stop + 1, step))
    return {v for v in items if lo <= v <= hi}, wildcard


def _num(token: str, lo: int, hi: int, names: dict[str, int] | None) -> int:
    token = token.strip().lower()
    if names and token in names:
        return names[token]
    try:
        value = int(token)
    except ValueError:
        raise AppError(f"cron 里有看不懂的值：{token}") from None
    if not lo <= value <= hi:
        raise AppError(f"cron 取值 {value} 超出范围 {lo}-{hi}")
    return value


def ensure_reachable(expr: str) -> str:
    """归一 + 校验 + 确认「真的会触发」。

    `0 0 31 2 *`（2 月 31 日）这种语法合法、但日历上永远不存在的 cron，如果放进去
    只会变成一条永远不跑、也不报错的死任务；这里在创建/修改时就拒掉。
    """
    spec = CronSpec(expr)
    if spec.next_after() is None:
        raise AppError(
            f"这个 cron 永远不会触发：{expr} —— 日期在日历上不存在"
            "（比如 2 月 30/31 日），换个日期或把「日」写成 *"
        )
    return spec.expr


class CronSpec:
    __slots__ = ("minutes", "hours", "days", "months", "dows", "dom_wild", "dow_wild", "expr")

    def __init__(self, expr: str):
        self.expr = normalize_cron(expr)
        fields = self.expr.split()
        self.minutes, _ = _expand(fields[0], 0, 59)
        self.hours, _ = _expand(fields[1], 0, 23)
        self.days, self.dom_wild = _expand(fields[2], 1, 31)
        self.months, _ = _expand(fields[3], 1, 12, _MONTH_NAMES)
        self.dows, self.dow_wild = _expand(fields[4], 0, 7, _DOW_NAMES)
        if 7 in self.dows:  # 7 也代表周日
            self.dows = (self.dows - {7}) | {0}
        if not (self.minutes and self.hours and self.days and self.months and self.dows):
            raise AppError(f"cron 展开后没有任何有效时间点：{self.expr}")

    def day_matches(self, day: datetime) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days
        cron_dow = (day.weekday() + 1) % 7  # datetime: 周一=0；cron: 周日=0
        dow_ok = cron_dow in self.dows
        if self.dom_wild or self.dow_wild:
            return dom_ok and dow_ok
        return dom_ok or dow_ok  # 两个都限定 → 任一命中（Vixie 规矩）

    def next_after(self, after: datetime | None = None) -> datetime | None:
        after = after or datetime.now()
        cursor = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        hours = sorted(self.hours)
        minutes = sorted(self.minutes)
        for _ in range(_MAX_SCAN_DAYS):
            if self.day_matches(cursor):
                same_day = cursor.hour
                for hour in hours:
                    if hour < same_day:
                        continue
                    for minute in minutes:
                        if hour == same_day and minute < cursor.minute:
                            continue
                        return cursor.replace(hour=hour, minute=minute, second=0, microsecond=0)
            cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return None


def human(cron: str) -> str:
    """给界面一句能看懂的时间描述。"""
    try:
        spec = CronSpec(cron)
    except AppError:
        return str(cron)
    fields = spec.expr.split()
    step = fields[0]
    minute, hour, dom, month, dow = fields
    if spec.expr == "* * * * *":
        return "每分钟"
    if minute == "0" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "每小时整点"
    if step.startswith("*/") and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return f"每 {step[2:]} 分钟"
    if hour.startswith("*/") and dom == "*" and month == "*" and dow == "*":
        return f"每 {hour[2:]} 小时"
    parts: list[str] = []
    if dom != "*":
        picked = sorted(spec.days)
        parts.append("每月 " + "、".join(str(d) for d in picked) + " 号")
    if dow != "*":
        names = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
        seen: list[str] = []
        for d in sorted(spec.dows):
            label = names[d]
            if label not in seen:
                seen.append(label)
        parts.append("、".join(seen))
    time_text = ""
    if minute.isdigit() and hour.isdigit():
        time_text = f"{int(hour):02d}:{int(minute):02d}"
    elif hour == "*" and minute.isdigit():
        time_text = f"每小时第 {int(minute)} 分"
    if parts:
        text = "、".join(parts) + (" " + time_text if time_text else "")
        if not spec.dom_wild and not spec.dow_wild:
            text += "（日期与星期满足任一）"
        return text
    return (time_text + " 每天") if time_text else f"cron {spec.expr}"


class ScheduleStore:
    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.settings = runtime.settings
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ---------------- 存储 ----------------
    def _path(self) -> Path:
        return Path(self.settings.data_dir) / "schedules.json"

    def _load(self) -> list[dict[str, Any]]:
        path = self._path()
        if not path.exists():
            return []
        try:
            raw = __import__("json").loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        tasks = raw.get("tasks") if isinstance(raw, dict) else raw
        return [t for t in (tasks or []) if isinstance(t, dict) and t.get("id")]

    def _save(self, tasks: list[dict[str, Any]]) -> None:
        import json

        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    # ---------------- 增删改查 ----------------
    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            tasks = self._load()
        out = []
        for task in tasks:
            item = dict(task)
            if task.get("enabled"):
                try:
                    nxt = CronSpec(task["cron"]).next_after()
                    item["next_run"] = nxt.strftime("%Y-%m-%d %H:%M") if nxt else ""
                except AppError:
                    item["next_run"] = ""
            else:
                item["next_run"] = ""
            item["human"] = human(task.get("cron", ""))
            item["running"] = bool(_RUNNING.get(task["id"]))
            out.append(item)
        return sorted(out, key=lambda x: str(x.get("created_at") or ""))

    def get(self, task_id: str) -> dict[str, Any]:
        for task in self.list():
            if task["id"] == task_id:
                return task
        raise NotFoundError(f"没有这个定时任务：{task_id}")

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip() or "未命名任务"
        # 归一 + 校验取值范围 + 确认日历上真的存在这个时间点
        cron = ensure_reachable(normalize_cron(body.get("cron")))
        action = self._normalize_action(body)
        task = {
            "id": new_id("sch"),
            "name": name[:60],
            "cron": cron,
            "action": action,
            "enabled": bool(body.get("enabled", True)),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_run": "",
            "last_status": "",
            "last_error": "",
            "last_output": "",
            "run_count": 0,
            "session_id": "",
        }
        with self._lock:
            tasks = self._load()
            if len(tasks) >= 200:
                raise AppError("定时任务最多 200 个，先删几个")
            tasks.append(task)
            self._save(tasks)
        logger.info("新建定时任务 %s（%s）", task["id"], task["cron"])
        return self.get(task["id"])

    def _normalize_action(self, body: dict[str, Any]) -> dict[str, Any]:
        kind = str(body.get("kind") or "chat").strip().lower()
        if kind in {"prompt", "message", "agent"}:
            kind = "chat"
        if kind not in {"chat", "workflow"}:
            raise AppError("kind 只能是 chat（跑一句话）或 workflow（跑工作流）")
        if kind == "chat":
            prompt = str(body.get("prompt") or "").strip()
            if not prompt:
                raise AppError("prompt 不能为空（这就是到点要它做的事）")
            return {"kind": "chat", "prompt": prompt[:4000]}
        workflow = str(body.get("workflow") or "").strip()
        if not workflow:
            raise AppError("workflow 不能为空（要跑哪个 workflows/*.json）")
        inputs = body.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            raise AppError("inputs 必须是对象")
        return {"kind": "workflow", "workflow": workflow, "inputs": inputs or {}}

    def update(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            tasks = self._load()
            target = next((t for t in tasks if t["id"] == task_id), None)
            if target is None:
                raise NotFoundError(f"没有这个定时任务：{task_id}")
            if body.get("name") is not None:
                target["name"] = str(body["name"]).strip()[:60] or target["name"]
            if body.get("cron") is not None:
                target["cron"] = ensure_reachable(normalize_cron(body["cron"]))
            if any(k in body for k in ("kind", "prompt", "workflow", "inputs")):
                merged = {**target.get("action", {}), **{k: body[k] for k in ("kind", "prompt", "workflow", "inputs") if k in body}}
                target["action"] = self._normalize_action(merged)
            if "enabled" in body:
                target["enabled"] = bool(body["enabled"])
            self._save(tasks)
        return self.get(task_id)

    def delete(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            tasks = self._load()
            remaining = [t for t in tasks if t["id"] != task_id]
            if len(remaining) == len(tasks):
                raise NotFoundError(f"没有这个定时任务：{task_id}")
            self._save(remaining)
        return {"ok": True, "deleted": task_id}

    def set_enabled(self, task_id: str, enabled: bool) -> dict[str, Any]:
        return self.update(task_id, {"enabled": bool(enabled)})

    # ---------------- 执行 ----------------
    def run_now(self, task_id: str, wait: bool = False) -> dict[str, Any]:
        task = self.get(task_id)
        if not wait and _RUNNING.get(task_id):
            return {"ok": False, "running": True, "message": "这个任务正在跑，等它跑完"}
        if wait:
            return self._execute(task)
        threading.Thread(target=self._execute_safe, args=(task,), daemon=True, name=f"schedule-{task_id}").start()
        return {"ok": True, "started": True, "task": task_id}

    def _execute_safe(self, task: dict[str, Any]) -> None:
        try:
            self._execute(task)
        except Exception:  # noqa: BLE001 - 后台线程不能把异常扔给主循环
            logger.exception("定时任务 %s 执行失败", task.get("id"))

    def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task["id"]
        with _RUNNING_LOCK:
            if _RUNNING.get(task_id):
                return {"ok": False, "running": True}
            _RUNNING[task_id] = True
        started = time.time()
        status, output, error = "ok", "", ""
        # 并发闸门：同一时刻最多 _MAX_CONCURRENT 个任务在跑，多的在这里排队。
        with _SLOTS:
            try:
                output = self._dispatch(task)
            except Exception as exc:  # noqa: BLE001
                status, error = "error", f"{type(exc).__name__}: {exc}"
                output = error
                logger.warning("定时任务 %s 失败：%s", task_id, exc)
            finally:
                with _RUNNING_LOCK:
                    _RUNNING.pop(task_id, None)
        elapsed = round(time.time() - started, 1)
        with self._lock:
            tasks = self._load()
            for item in tasks:
                if item["id"] != task_id:
                    continue
                item["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                item["last_status"] = status
                item["last_error"] = error[:500]
                item["last_output"] = str(output)[:2000]
                item["last_seconds"] = elapsed
                item["run_count"] = int(item.get("run_count") or 0) + 1
                if item.get("_session"):
                    item["session_id"] = item.pop("_session")
            self._save(tasks)
        return {"ok": status == "ok", "status": status, "output": output, "error": error, "seconds": elapsed}

    def _session_for(self, task: dict[str, Any]) -> Any:
        from .sessions import Session

        existing = task.get("session_id")
        if existing:
            try:
                return self.runtime.sessions.get(existing)
            except Exception:  # noqa: BLE001 - 会话被删了就新建一个
                pass
        return self.runtime.sessions.create(title=f"[定时] {task['name']}")

    def _dispatch(self, task: dict[str, Any]) -> str:
        from .tools.base import ToolContext

        action = task.get("action") or {}
        session = self._session_for(task)
        with self._lock:
            tasks = self._load()
            for item in tasks:
                if item["id"] == task["id"]:
                    item["_session"] = session.id
            self._save(tasks)
        ctx = ToolContext(session=session, agent_id=f"schedule:{task['id']}", depth=0, allow_spawn=False)

        if action.get("kind") == "workflow":
            try:
                result = self.runtime.workflows.run(str(action.get("workflow")), action.get("inputs") or {}, ctx)
                text = str(result.get("final") or "")
            except Exception as exc:  # noqa: BLE001 - 失败也让会话里有记录
                self._note_failure(session, task, exc)
                raise
            session.messages.append({"role": "assistant", "content": f"[定时任务] {task['name']}：{text}"})
            self.runtime.sessions.save(session)
            return text or "（工作流跑完了，但没有最终输出）"

        from .agent import Agent

        prompt = str(action.get("prompt") or "")
        try:
            result = Agent(self.runtime, session, ctx).run(prompt)
        except Exception as exc:  # noqa: BLE001 - 同上：会话里留一条失败记录
            self._note_failure(session, task, exc)
            raise
        self.runtime.sessions.save(session)
        return result.content

    def _note_failure(self, session: Any, task: dict[str, Any], exc: Exception) -> None:
        try:
            session.messages.append({
                "role": "assistant",
                "content": f"[定时任务] {task['name']} 这次没跑成功：{type(exc).__name__}: {exc}",
            })
            self.runtime.sessions.save(session)
        except Exception:  # noqa: BLE001 - 记录失败本身不能再炸
            logger.exception("写入失败记录时出错")

    # ---------------- 后台循环 ----------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="schedule-loop", daemon=True)
        self._thread.start()
        logger.info("定时任务调度器已启动（每 %ss 检查一次）", _TICK_SECONDS)

    def _loop(self) -> None:
        # id -> (cron, 下次该跑的时间)。把 cron 一起存着，是因为用户改期之后
        # 必须立刻按新 cron 重算——否则会傻等旧时间点（界面显示「每分钟」但
        # 实际要等到明天）。
        next_at: dict[str, tuple[str, datetime | None]] = {}
        while True:
            try:
                tasks = self.list()
                now = datetime.now().replace(second=0, microsecond=0)
                live = {t["id"] for t in tasks}
                for tid in list(next_at):
                    if tid not in live:
                        next_at.pop(tid, None)
                for task in tasks:
                    tid = task["id"]
                    if not task.get("enabled"):
                        next_at.pop(tid, None)
                        continue
                    entry = next_at.get(tid)
                    if entry is None or entry[0] != task["cron"]:
                        # 没缓存过，或者 cron 刚被改：重算。允许命中原分钟（不丢这一分钟）。
                        try:
                            slot = CronSpec(task["cron"]).next_after(now - timedelta(minutes=1))
                        except AppError:
                            slot = None
                        entry = (task["cron"], slot)
                        next_at[tid] = entry
                    slot = entry[1]
                    if slot is not None and slot <= now:
                        # 重启时 next_after(now-1min) 会命中「当前这一分钟」，如果这
                        # 一分钟其实已经跑过了，就别再补一次。
                        already = str(task.get("last_run") or "")[:16] == now.strftime("%Y-%m-%d %H:%M")
                        if not already and not _RUNNING.get(tid):
                            logger.info("触发定时任务 %s（%s）", tid, task.get("cron"))
                            threading.Thread(
                                target=self._execute_safe, args=(task,), daemon=True,
                                name=f"schedule-{tid}",
                            ).start()
                        try:
                            next_at[tid] = (task["cron"], CronSpec(task["cron"]).next_after(now))
                        except AppError:
                            next_at.pop(tid, None)
            except Exception:  # noqa: BLE001
                logger.exception("调度循环出错")
            time.sleep(_TICK_SECONDS)
