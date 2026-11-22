"""定时任务工具：让模型能自己排班，而不是靠用户记着每天去催它。"""

from __future__ import annotations

import json
from typing import Any

from ..errors import AppError, NotFoundError
from .base import ToolContext, ToolRegistry


def register(registry: ToolRegistry, runtime: Any) -> None:
    def schedule_list(ctx: ToolContext, args: dict[str, Any]) -> str:
        tasks = runtime.schedules.list()
        if not tasks:
            return "还没有定时任务。用 schedule_create 建一个（cron 写法如 0 9 * * * = 每天 9 点）。"
        rows = []
        for task in tasks:
            action = task.get("action") or {}
            what = action.get("prompt") or f"工作流 {action.get('workflow')}"
            rows.append(
                {
                    "id": task["id"],
                    "名字": task["name"],
                    "时间": task.get("human") or task["cron"],
                    "cron": task["cron"],
                    "启用": bool(task.get("enabled")),
                    "下次": task.get("next_run") or "—",
                    "上次": task.get("last_run") or "还没跑过",
                    "上次结果": task.get("last_status") or "—",
                    "跑过次数": task.get("run_count") or 0,
                    "内容": str(what)[:80],
                    "会话": task.get("session_id") or "",
                }
            )
        return json.dumps(rows, ensure_ascii=False, indent=2)

    def schedule_create(ctx: ToolContext, args: dict[str, Any]) -> str:
        try:
            body = {
                "name": args.get("name"),
                "cron": args.get("cron"),
                "kind": args.get("kind") or "chat",
                "prompt": args.get("prompt"),
                "workflow": args.get("workflow"),
                "inputs": args.get("inputs"),
                "enabled": True,
            }
            task = runtime.schedules.create(body)
        except AppError as exc:
            return f"ERROR: {exc}"
        return (
            f"已创建定时任务 {task['id']}：{task['name']}，{task['human']}，"
            f"下次 {task.get('next_run') or '（未计算）'}。到点会跑在会话 [定时] {task['name']} 里。"
        )

    def schedule_update(ctx: ToolContext, args: dict[str, Any]) -> str:
        task_id = str(args.get("id") or "").strip()
        if not task_id:
            return "ERROR: 要传 id（先用 schedule_list 看）"
        body: dict[str, Any] = {}
        for key in ("name", "cron", "prompt", "workflow", "inputs"):
            if args.get(key) not in (None, ""):
                body[key] = args[key]
        if args.get("enabled") is not None:
            body["enabled"] = bool(args["enabled"])
        if not body:
            return "ERROR: 没给要改什么（name / cron / prompt / workflow / inputs / enabled）"
        try:
            task = runtime.schedules.update(task_id, body)
        except (AppError, NotFoundError) as exc:
            return f"ERROR: {exc}"
        return f"已更新 {task['id']}：{task['name']}，{task['human']}，启用={task.get('enabled')}"

    def schedule_delete(ctx: ToolContext, args: dict[str, Any]) -> str:
        task_id = str(args.get("id") or "").strip()
        if not task_id:
            return "ERROR: 要传 id"
        try:
            runtime.schedules.delete(task_id)
        except (AppError, NotFoundError) as exc:
            return f"ERROR: {exc}"
        return f"已删除定时任务 {task_id}"

    def schedule_run(ctx: ToolContext, args: dict[str, Any]) -> str:
        task_id = str(args.get("id") or "").strip()
        if not task_id:
            return "ERROR: 要传 id"
        try:
            result = runtime.schedules.run_now(task_id, wait=True)
        except (AppError, NotFoundError) as exc:
            return f"ERROR: {exc}"
        if result.get("ok"):
            return "跑完了，输出：\n" + str(result.get("output") or "")[:1500]
        return "ERROR: " + str(result.get("error") or result.get("message") or "执行失败")

    common_id = {"type": "string", "description": "定时任务 id（schedule_list 里能看到）"}

    registry.register(
        "schedule_list",
        "列出所有定时任务（名字、cron、下次运行时间、上次结果）。想知道「现在有哪些定时任务」就用它。",
        {"type": "object", "properties": {}},
        schedule_list,
    )
    registry.register(
        "schedule_create",
        "创建一个定时任务：到点自动跑一句话（kind=chat，给 prompt），或跑一个工作流（kind=workflow，给 workflow）。"
        "cron 是 5 段（分 时 日 月 周），也支持 @daily / @hourly / @every 10m / @every 2h。"
        "用户说「每天 9 点帮我做 X」时用它。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "任务名字，简短好认，例如「每天早上读日报」"},
                "cron": {"type": "string", "description": "5 段 cron 或 @daily/@hourly/@weekly/@monthly/@every 10m，例如 0 9 * * *"},
                "kind": {"type": "string", "description": "chat（默认，跑一句话）或 workflow（跑工作流）"},
                "prompt": {"type": "string", "description": "kind=chat 时要它做的事，写清楚、可独立执行"},
                "workflow": {"type": "string", "description": "kind=workflow 时 workflows/ 下的工作流名"},
                "inputs": {"type": "object", "description": "kind=workflow 时的输入参数"},
            },
            "required": ["name", "cron"],
        },
        schedule_create,
    )
    registry.register(
        "schedule_update",
        "改一个定时任务：改名字、改时间、改内容、或开关它（enabled=false 就是暂停）。",
        {
            "type": "object",
            "properties": {
                "id": common_id,
                "name": {"type": "string"},
                "cron": {"type": "string"},
                "prompt": {"type": "string"},
                "workflow": {"type": "string"},
                "inputs": {"type": "object"},
                "enabled": {"type": "boolean", "description": "true 启用 / false 暂停"},
            },
            "required": ["id"],
        },
        schedule_update,
    )
    registry.register(
        "schedule_delete",
        "删掉一个定时任务（不可恢复，先 schedule_list 确认 id）。",
        {"type": "object", "properties": {"id": common_id}, "required": ["id"]},
        schedule_delete,
    )
    registry.register(
        "schedule_run",
        "立刻跑一次某个定时任务（不等它的时间点），用来验证任务写得对不对。会阻塞到跑完。",
        {"type": "object", "properties": {"id": common_id}, "required": ["id"]},
        schedule_run,
    )
