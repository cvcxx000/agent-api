"""工作流工具。"""

from __future__ import annotations

import json
from typing import Any

from ..errors import ToolError
from ..utils import truncate
from .base import ToolContext, ToolRegistry


def register(registry: ToolRegistry, get_engine) -> None:
    def list_workflows(ctx: ToolContext, args: dict[str, Any]) -> str:
        items = get_engine().list()
        return json.dumps(items, ensure_ascii=False, indent=2) if items else "当前没有工作流（在 workflows/ 放 .json 即可）"

    def run_workflow(ctx: ToolContext, args: dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip()
        if not name:
            raise ToolError("name 不能为空")
        inputs = args.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise ToolError("inputs 必须是对象")
        result = get_engine().run(name, inputs, ctx)
        outputs = {k: truncate(v, 8000) if isinstance(v, str) else v for k, v in result["outputs"].items()}
        payload = {
            "workflow": result["workflow"],
            "inputs": result["inputs"],
            "outputs": outputs,
            "final": truncate(result["final"], 12000),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    registry.register(
        "list_workflows",
        "列出 workflows/ 目录下可用的工作流及其步骤。",
        {"type": "object", "properties": {}},
        list_workflows,
    )
    registry.register(
        "run_workflow",
        "按名字执行一个 JSON 工作流，返回每个 step 的输出。"
        "步骤类型支持 tool / python / agent，支持 {{ inputs.x }}、{{ steps.s1.output }} 插值。",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}, "inputs": {"type": "object", "description": "工作流入参"}},
            "required": ["name"],
        },
        run_workflow,
    )
