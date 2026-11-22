"""轻量工作流引擎：JSON 里按顺序编排 tool / python / agent 步骤，支持 {{ }} 变量插值。

用 JSON 而不是 YAML，是为了不在手机端装 PyYAML。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ToolError
from .sessions import Session
from .utils import new_id, truncate

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.\[\]-]+)\s*\}\}")


def _lookup(data: Any, expr: str) -> Any:
    current = data
    for part in expr.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ToolError(f"模板变量不存在：{{{{ {expr} }}}}")
    return current


def render_value(value: Any, data: dict[str, Any]) -> Any:
    if isinstance(value, str):
        full = _PLACEHOLDER.fullmatch(value.strip())
        if full:
            return _lookup(data, full.group(1))
        return _PLACEHOLDER.sub(lambda m: str(_lookup(data, m.group(1))), value)
    if isinstance(value, list):
        return [render_value(v, data) for v in value]
    if isinstance(value, dict):
        return {k: render_value(v, data) for k, v in value.items()}
    return value


def _step_kind(step: dict[str, Any]) -> str:
    for key in ("tool", "python", "agent"):
        if key in step:
            return key
    return "unknown"


@dataclass
class WorkflowDef:
    name: str
    description: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str | None = None
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputs": self.inputs,
            "steps": [{"id": s.get("id"), "kind": _step_kind(s)} for s in self.steps],
            "path": self.path,
        }


class WorkflowEngine:
    def __init__(self, runtime: Any, directory: Path):
        self.runtime = runtime
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _files(self) -> list[Path]:
        return sorted(p for p in self.directory.glob("*") if p.suffix == ".json")

    def _load_file(self, path: Path) -> WorkflowDef:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolError(f"工作流 {path.name} 不是合法 JSON：{exc}") from exc
        if not isinstance(raw, dict):
            raise ToolError(f"{path.name} 顶层必须是对象")
        name = str(raw.get("name") or path.stem)
        steps = raw.get("steps") or []
        if not isinstance(steps, list) or not steps:
            raise ToolError(f"{path.name} 缺少 steps")
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ToolError(f"{path.name} 第 {index + 1} 个 step 必须是对象")
            if not any(k in step for k in ("tool", "python", "agent")):
                raise ToolError(f"{path.name} 第 {index + 1} 个 step 需要 tool / python / agent 之一")
            step.setdefault("id", f"step{index + 1}")
        return WorkflowDef(
            name=name,
            description=str(raw.get("description") or ""),
            inputs=raw.get("inputs") or {},
            steps=steps,
            system_prompt=raw.get("system_prompt"),
            path=str(path),
        )

    def load_all(self) -> dict[str, WorkflowDef]:
        result: dict[str, WorkflowDef] = {}
        for path in self._files():
            defn = self._load_file(path)
            result[defn.name] = defn
        return result

    def list(self) -> list[dict[str, Any]]:
        return [w.to_dict() for w in self.load_all().values()]

    def get(self, name: str) -> WorkflowDef:
        layout = self.load_all()
        if name in layout:
            return layout[name]
        lowered = {k.lower(): k for k in layout}
        if name.lower() in lowered:
            return layout[lowered[name.lower()]]
        raise NotFoundError(f"未找到工作流：{name}，可用：{', '.join(layout) or '（无）'}")

    def _prepare_inputs(self, defn: WorkflowDef, provided: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, spec in (defn.inputs or {}).items():
            if isinstance(spec, dict):
                if key in provided:
                    result[key] = provided[key]
                elif "default" in spec:
                    result[key] = spec["default"]
                elif spec.get("required"):
                    raise ToolError(f"工作流 {defn.name} 缺少必填输入：{key}")
            elif key in provided:
                result[key] = provided[key]
        for key, value in provided.items():
            result.setdefault(key, value)
        return result

    def run(self, name: str, inputs: dict[str, Any] | None, ctx: Any) -> dict[str, Any]:
        defn = self.get(name)
        resolved_inputs = self._prepare_inputs(defn, inputs or {})
        data: dict[str, Any] = {"inputs": resolved_inputs, "steps": {}}
        outputs: dict[str, Any] = {}

        for step in defn.steps:
            step_id = str(step["id"])
            kind = _step_kind(step)
            if kind == "tool":
                args = render_value(step.get("args") or {}, data)
                if not isinstance(args, dict):
                    raise ToolError(f"step {step_id} 的 args 必须是对象")
                output = self.runtime.registry.execute(str(step["tool"]), args, ctx)
            elif kind == "python":
                code = render_value(step["python"], data)
                output = self.runtime.registry.execute("run_python", {"code": str(code)}, ctx)
            else:  # agent
                output = self._run_agent_step(defn, step, data, ctx)

            data["steps"][step_id] = {"output": output, "ok": not (isinstance(output, str) and output.startswith("ERROR:"))}
            outputs[step_id] = output
            if isinstance(output, str) and output.startswith("ERROR:") and step.get("stop_on_error", True):
                break

        final = outputs.get(str(defn.steps[-1]["id"]), "")
        return {"workflow": defn.name, "inputs": resolved_inputs, "outputs": outputs, "final": final}

    def _run_agent_step(self, defn: WorkflowDef, step: dict[str, Any], data: dict[str, Any], ctx: Any) -> str:
        from .agent import Agent
        from .tools.base import ToolContext

        prompt_text = render_value(step["agent"], data)
        session = Session(
            id=new_id("wf"),
            title=f"[工作流] {defn.name} / {step['id']}",
            system_prompt=defn.system_prompt or self.runtime.default_system_prompt,
            kind="workflow",
            persist=False,
        )
        sub_ctx = ToolContext(
            session=session,
            agent_id=f"wf-{step['id']}",
            depth=ctx.depth + 1,
            allow_spawn=False,  # 工作流步骤内不再派子 Agent，避免并发失控
            parent_session_id=ctx.session.id,
        )
        agent = Agent(self.runtime, session, sub_ctx, max_turns=8)
        return truncate(agent.run(str(prompt_text)).content, 20000)
