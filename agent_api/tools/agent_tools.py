"""Agent 自管理工具：派子任务、等待/取消、修改系统提示词、查看工具清单。"""

from __future__ import annotations

import json
from typing import Any

from ..config import settings
from ..errors import ToolError
from ..utils import truncate
from .base import ToolContext, ToolRegistry


def register(registry: ToolRegistry, get_runtime) -> None:
    def spawn_agent(ctx: ToolContext, args: dict[str, Any]) -> str:
        runtime = get_runtime()
        task = str(args.get("task") or "").strip()
        if not task:
            raise ToolError("task 不能为空：请把要交给子 Agent 的具体任务写清楚")
        record = runtime.subagents.spawn(
            parent=ctx.session,
            task=task,
            system_prompt=str(args.get("system_prompt") or "").strip() or None,
            title=str(args.get("title") or "").strip(),
            depth=ctx.depth + 1,
        )
        return json.dumps(
            {
                "sub_id": record.id,
                "status": record.status,
                "concurrency": f"{runtime.subagents.active_count}/{runtime.subagents.max_concurrent}",
                "next": f"用 wait_agent(sub_id='{record.id}') 取结果；或用 list_agents 查看全部。",
            },
            ensure_ascii=False,
            indent=2,
        )

    def wait_agent(ctx: ToolContext, args: dict[str, Any]) -> str:
        runtime = get_runtime()
        sub_id = str(args.get("sub_id") or "").strip()
        if not sub_id:
            raise ToolError("sub_id 不能为空")
        timeout = max(0.0, min(float(args.get("timeout") or 300), 600.0))
        record = runtime.subagents.wait(sub_id, timeout=timeout)
        data = record.to_dict(include_result=True)
        data["concurrency"] = f"{runtime.subagents.active_count}/{runtime.subagents.max_concurrent}"
        if record.status == "running":
            data["hint"] = f"子 Agent 还在跑（已等 {timeout:g}s），可以稍后再 wait_agent，或先做别的事。"
        data["result"] = truncate(data.get("result") or "", 20000)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def list_agents(ctx: ToolContext, args: dict[str, Any]) -> str:
        runtime = get_runtime()
        return json.dumps(
            {
                "active": runtime.subagents.active_count,
                "max_concurrent": runtime.subagents.max_concurrent,
                "max_depth": runtime.subagents.max_depth,
                "agents": runtime.subagents.list(),
            },
            ensure_ascii=False,
            indent=2,
        )

    def cancel_agent(ctx: ToolContext, args: dict[str, Any]) -> str:
        runtime = get_runtime()
        sub_id = str(args.get("sub_id") or "").strip()
        if not sub_id:
            raise ToolError("sub_id 不能为空")
        return json.dumps(runtime.subagents.cancel(sub_id).to_dict(), ensure_ascii=False, indent=2)

    def set_system_prompt(ctx: ToolContext, args: dict[str, Any]) -> str:
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ToolError("prompt 不能为空")
        mode = str(args.get("mode") or "replace").lower()
        if mode not in {"replace", "append"}:
            raise ToolError("mode 只能是 replace 或 append")
        limit = max(1000, int(settings.system_max_chars))
        if len(prompt) > limit:
            raise ToolError(
                f"prompt 太长（{len(prompt)} 字符），单次上限 {limit} 字符；"
                "请先精简，或拆成多次 append。"
            )
        if mode == "append" and len(ctx.session.system_prompt or "") + len(prompt) > limit:
            raise ToolError(
                f"追加后系统提示词会超过 {limit} 字符；先用 mode=replace 精简，再追加。"
            )
        ctx.session.append_system_prompt(prompt, mode=mode)
        get_runtime().sessions.save(ctx.session)
        return (
            f"OK: 系统提示词已{'追加' if mode == 'append' else '替换'}，"
            f"当前共 {len(ctx.session.system_prompt)} 字符，下一轮生效。"
        )

    def get_system_prompt(ctx: ToolContext, args: dict[str, Any]) -> str:
        total = len(ctx.session.system_prompt or "")
        try:
            offset = max(0, int(args.get("offset") or 0))
            limit = max(200, min(int(args.get("limit") or 4000), 8000))
        except (TypeError, ValueError):
            offset, limit = 0, 4000
        chunk = (ctx.session.system_prompt or "")[offset : offset + limit]
        if offset + limit < total:
            chunk += f"\n\n...[还有 {total - offset - limit} 字符，用 offset={offset + limit} 继续读]"
        return chunk or f"系统提示词为空（共 {total} 字符）"

    def list_tools(ctx: ToolContext, args: dict[str, Any]) -> str:
        return json.dumps(get_runtime().registry.describe(), ensure_ascii=False, indent=2)

    registry.register(
        "spawn_agent",
        "派发一个子 Agent，在独立会话里并行完成子任务，立即返回 sub_id（不阻塞）。"
        "服务端最多同时运行 3 个子 Agent，满了会直接报错。适合可并行的独立子任务。",
        {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "子任务描述，要包含目标、输入、期望产出"},
                "system_prompt": {"type": "string", "description": "可选：给子 Agent 的额外角色设定"},
                "title": {"type": "string", "description": "可选：子任务短标题"},
            },
            "required": ["task"],
        },
        spawn_agent,
        requires_spawn=True,
    )
    registry.register(
        "wait_agent",
        "等待某个子 Agent 结束并取回结果（带超时，超时后返回 running 状态，不会取消它）。",
        {
            "type": "object",
            "properties": {
                "sub_id": {"type": "string"},
                "timeout": {"type": "number", "description": "最多等多少秒，默认 300，上限 600"},
            },
            "required": ["sub_id"],
        },
        wait_agent,
    )
    registry.register(
        "list_agents",
        "查看所有子 Agent 的状态、并发占用（active/max）和结果摘要。",
        {"type": "object", "properties": {}},
        list_agents,
    )
    registry.register(
        "cancel_agent",
        "取消一个还在运行的子 Agent。",
        {"type": "object", "properties": {"sub_id": {"type": "string"}}, "required": ["sub_id"]},
        cancel_agent,
    )
    registry.register(
        "set_system_prompt",
        "修改当前会话的系统提示词（replace 覆盖 / append 追加），下一轮生效。",
        {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "mode": {"type": "string", "enum": ["replace", "append"], "default": "replace"},
            },
            "required": ["prompt"],
        },
        set_system_prompt,
    )
    registry.register(
        "get_system_prompt",
        "分段查看当前会话的系统提示词（默认只给 4000 字符，避免把上下文一次撑爆）。",
        {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "从第几个字符开始，默认 0"},
                "limit": {"type": "integer", "description": "本次最多返回多少字符，默认 4000，上限 8000"},
            },
        },
        get_system_prompt,
    )
    registry.register(
        "list_tools",
        "列出当前进程注册的所有工具及其参数说明。",
        {"type": "object", "properties": {}},
        list_tools,
    )
