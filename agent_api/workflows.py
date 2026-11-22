"""JSON 工作流引擎：多个步骤依次调用工具，支持 {{steps.xxx.result}} 变量插值。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .errors import ToolError, ValidationError

logger = logging.getLogger("agent.workflows")

_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class CtxProxy:
    """工作流步骤自己不能派子 Agent，所以子 Agent 工具走代理时直接报错。"""

    spawn_blocked = True

    def __init__(self, ctx: Any):
        self._ctx = ctx

    def __getattr__(self, item: str) -> Any:
        return getattr(self._ctx, item)


class WorkflowEngine:
    def __init__(self, runtime: Any, root: Path):
        self.runtime = runtime
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("工作流文件损坏 %s: %s", path, exc)
                continue
            items.append(
                {
                    "name": path.stem,
                    "title": data.get("title") or path.stem,
                    "description": data.get("description", ""),
                    "steps": [s.get("name") for s in data.get("steps", []) if isinstance(s, dict)],
                }
            )
        return items

    def load(self, name: str) -> dict[str, Any]:
        safe = str(name or "").strip()
        if not safe or "/" in safe or "\\" in safe or safe.startswith("."):
            raise ValidationError(f"非法的工作流名称：{name}")
        path = self.root / f"{safe}.json"
        if not path.exists():
            raise ToolError(f"工作流不存在：{safe}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolError(f"工作流 JSON 不合法：{exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            raise ToolError("工作流格式错误：需要 {\"steps\": [...]}")
        return data

    def run(self, name: str, inputs: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        workflow = self.load(name)
        inputs = inputs or {}
        variables: dict[str, Any] = {"inputs": inputs}
        steps_out: dict[str, Any] = {}
        for index, step in enumerate(workflow.get("steps", [])):
            if not isinstance(step, dict):
                raise ToolError(f"第 {index + 1} 步不是对象")
            step_name = str(step.get("name") or f"step{index + 1}")
            tool = str(step.get("tool") or "")
            if not tool:
                raise ToolError(f"步骤 {step_name} 缺少 tool 字段")
            args = self._render(step.get("args") or {}, variables)
            if not isinstance(args, dict):
                raise ToolError(f"步骤 {step_name} 的 args 必须是对象")
            result = self.runtime.registry.execute(tool, args, CtxProxy(ctx) if ctx is not None else ctx)
            error = result.startswith("ERROR:")
            steps_out[step_name] = {"tool": tool, "args": args, "result": result, "error": error}
            variables.setdefault("steps", {})[step_name] = steps_out[step_name]
            logger.info("工作流 %s 步骤 %s -> %s", name, step_name, "ERROR" if error else "ok")
        return {"workflow": name, "steps": steps_out, "ok": not any(s.get("error") for s in steps_out.values())}

    def _render(self, value: Any, variables: dict[str, Any]) -> Any:
        if isinstance(value, str):
            whole = _VAR.fullmatch(value.strip())
            if whole:
                return self._lookup(whole.group(1), variables)

            def repl(match: re.Match[str]) -> str:
                got = self._lookup(match.group(1), variables)
                return got if isinstance(got, str) else json.dumps(got, ensure_ascii=False)

            return _VAR.sub(repl, value)
        if isinstance(value, dict):
            return {k: self._render(v, variables) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render(v, variables) for v in value]
        return value

    def _lookup(self, path: str, variables: dict[str, Any]) -> Any:
        current: Any = variables
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise ToolError(f"变量不存在：{{{{{path}}}}}")
        return current
