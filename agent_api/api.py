"""全部 HTTP 接口实现（路由表装配在这里）。"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from typing import Any

from .agent import Agent
from .errors import AppError, NotFoundError
from .sessions import Session, SubagentRecord
from .server import Request, Response, Router, StreamResponse, json_response
from .tools.base import ToolContext

logger = logging.getLogger("agent.api")

SSE_PING_SECONDS = float(os.getenv("SSE_PING_SECONDS") or 10)


def _sse_stream(events: "queue.Queue", sentinel: Any, ping: float | None = None) -> Any:
    interval = SSE_PING_SECONDS if ping is None else ping
    while True:
        try:
            event = events.get(timeout=interval)
        except queue.Empty:
            yield ": ping\n\n"
            continue
        if event is sentinel:
            break
        yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _ctx(runtime: Any, session: Session | None = None) -> ToolContext:
    return ToolContext(
        session=session or Session(id="api-direct", title="API 直调", persist=False),
        agent_id="api",
        allow_spawn=False,
    )


def _result(output: str) -> Response:
    return json_response({"ok": not output.startswith("ERROR:"), "result": output})


def _body_str(body: dict[str, Any], key: str, required: bool = True, default: str = "") -> str:
    value = body.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise AppError(f"{key} 必须是字符串")
    if required and not value.strip():
        raise AppError(f"{key} 不能为空")
    return value


def build_router(runtime: Any) -> Router:
    router = Router()

    # ---------------- 元信息 ----------------
    def health(req: Request, params: dict[str, str]) -> Response:
        return json_response({"ok": True, "service": "agent-api", "mock_llm": runtime.settings.use_mock_llm})

    def config(req: Request, params: dict[str, str]) -> Response:
        st = runtime.settings
        return json_response(
            {
                "model": st.llm_model,
                "base_url": st.llm_base_url,
                "mock_llm": st.use_mock_llm,
                "max_turns": st.max_turns,
                "max_subagents": st.max_subagents,
                "max_subagent_depth": st.max_subagent_depth,
                "sandbox_root": str(st.sandbox_root),
                "skills_dirs": [str(p) for p in st.skills_dirs],
                "workflows_dir": str(st.workflows_dir),
                "enable_python": st.enable_python,
                "enable_shell": st.enable_shell,
                "auth_enabled": st.auth_enabled,
            }
        )

    def tools(req: Request, params: dict[str, str]) -> Response:
        return json_response({"tools": runtime.registry.describe()})

    router.add("GET", "/health", health, auth=False)
    router.add("GET", "/api/config", config)
    router.add("GET", "/api/tools", tools)

    # ---------------- 对话 ----------------
    def _prepare_session(body: dict[str, Any]) -> Session:
        session_id = body.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise AppError("session_id 必须是字符串")
        session = runtime.sessions.get_or_create(
            session_id,
            title=str(body.get("title") or ""),
            system_prompt=body.get("system_prompt") if isinstance(body.get("system_prompt"), str) else None,
        )
        if session_id:
            if body.get("title"):
                session.title = str(body["title"])
            if isinstance(body.get("system_prompt"), str) and body["system_prompt"].strip():
                session.append_system_prompt(body["system_prompt"], mode="replace")
        return session

    def _attachments(body: dict[str, Any]) -> list[dict[str, Any]]:
        raw = body.get("attachments")
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for item in raw[:3]:
            if isinstance(item, str) and item.strip():
                out.append({"path": item.strip(), "name": "", "type": "file"})
            elif isinstance(item, dict) and item.get("path"):
                out.append(
                    {
                        "path": str(item["path"]).strip(),
                        "name": str(item.get("name") or ""),
                        "type": str(item.get("type") or "image"),
                    }
                )
        return out

    def _with_attachments(text: str, atts: list[dict[str, Any]]) -> str:
        if not atts:
            return text
        lines = [f"- {a['path']}（{a.get('type') or '文件'}）" for a in atts]
        return text + "\n\n[附件（都在 workspace 下）]\n" + "\n".join(lines)

    def chat(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise AppError("message 不能为空")
        session = _prepare_session(body)
        atts = _attachments(body)
        max_turns = body.get("max_turns")
        ctx = ToolContext(session=session, agent_id="main", depth=0, allow_spawn=True)
        agent = Agent(runtime, session, ctx, max_turns=int(max_turns) if max_turns else None)
        result = agent.run(_with_attachments(message, atts), attachments=atts)
        return json_response(
            {
                "session_id": session.id,
                "content": result.content,
                "turns": result.turns,
                "steps": result.steps,
                "message_count": len(session.messages),
                "subagents_active": runtime.subagents.active_count,
                "subagents_max": runtime.subagents.max_concurrent,
            }
        )

    def chat_stream(req: Request, params: dict[str, str]) -> Response | StreamResponse:
        body = req.json()
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise AppError("message 不能为空")
        session = _prepare_session(body)
        atts = _attachments(body)
        max_turns = body.get("max_turns")
        ctx = ToolContext(session=session, agent_id="main", depth=0, allow_spawn=True)
        agent = Agent(runtime, session, ctx, max_turns=int(max_turns) if max_turns else None)

        events: queue.Queue = queue.Queue()
        sentinel = object()

        def worker() -> None:
            try:
                result = agent.run(_with_attachments(message, atts), emit=events.put, attachments=atts)
                events.put(
                    {"type": "final", "session_id": session.id, "content": result.content, "turns": result.turns}
                )
            except AppError as exc:
                events.put({"type": "error", "message": exc.message})
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, ConnectionError) as exc:
                logger.warning("chat-stream 连接中断：%s", exc)
                events.put({"type": "error", "message": "连接中断，这一轮没跑完。重新发一次就好。"})
            except Exception as exc:  # noqa: BLE001
                events.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                events.put(sentinel)

        threading.Thread(target=worker, name=f"chat-stream-{session.id}", daemon=True).start()

        return StreamResponse(_sse_stream(events, sentinel))

    router.add("POST", "/api/chat", chat)
    router.add("POST", "/api/chat/stream", chat_stream)

    # ---------------- 会话 ----------------
    def list_sessions(req: Request, params: dict[str, str]) -> Response:
        return json_response({"sessions": runtime.sessions.list(kind=req.q("kind"))})

    def create_session(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        session = runtime.sessions.create(
            title=str(body.get("title") or ""),
            system_prompt=body.get("system_prompt") if isinstance(body.get("system_prompt"), str) else None,
        )
        return json_response(session.to_dict(include_messages=False))

    def get_session(req: Request, params: dict[str, str]) -> Response:
        session = runtime.sessions.get(params["session_id"])
        return json_response(session.to_dict(include_messages=req.q_bool("include_messages")))

    def delete_session(req: Request, params: dict[str, str]) -> Response:
        runtime.sessions.delete(params["session_id"])
        return json_response({"ok": True, "deleted": params["session_id"]})

    def session_messages(req: Request, params: dict[str, str]) -> Response:
        session = runtime.sessions.get(params["session_id"])
        limit = req.q_int("limit", 200, lo=1, hi=2000)
        return json_response({"session_id": session.id, "count": len(session.messages), "messages": session.messages[-limit:]})

    def session_prompt(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        session = runtime.sessions.get(params["session_id"])
        prompt = _body_str(body, "prompt")
        mode = str(body.get("mode") or "replace").lower()
        if mode not in {"replace", "append"}:
            raise AppError("mode 只能是 replace 或 append")
        session.append_system_prompt(prompt, mode=mode)
        runtime.sessions.save(session)
        return json_response({"ok": True, "chars": len(session.system_prompt), "mode": mode})

    def session_load_skill(req: Request, params: dict[str, str]) -> Response:
        session = runtime.sessions.get(params["session_id"])
        name = params["name"]
        runtime.skills.refresh()
        info = runtime.skills.get(name)
        if info.name in session.loaded_skills:
            return json_response({"ok": True, "already_loaded": True, "skill": info.name})
        content = runtime.skills.read(info.name)
        session.append_system_prompt(
            f"\n\n## 已载入技能：{info.name}\n> 技能目录：{info.directory}\n\n{content}", mode="append"
        )
        session.loaded_skills.append(info.name)
        runtime.sessions.save(session)
        return json_response({"ok": True, "skill": info.name, "chars": len(content)})

    router.add("GET", "/api/sessions", list_sessions)
    router.add("POST", "/api/sessions", create_session)
    router.add("GET", "/api/sessions/{session_id}", get_session)
    router.add("DELETE", "/api/sessions/{session_id}", delete_session)
    router.add("GET", "/api/sessions/{session_id}/messages", session_messages)
    router.add("POST", "/api/sessions/{session_id}/system-prompt", session_prompt)
    router.add("POST", "/api/sessions/{session_id}/skills/{name}", session_load_skill)

    # ---------------- 文件 / 代码 ----------------
    def read_file(req: Request, params: dict[str, str]) -> Response:
        return _result(
            runtime.registry.execute(
                "read_file",
                {"path": req.q("path", ""), "offset": req.q_int("offset", 1, lo=1), "limit": req.q_int("limit", 500, lo=1, hi=5000)},
                _ctx(runtime),
            )
        )

    def write_file(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        return _result(
            runtime.registry.execute(
                "write_file",
                {"path": _body_str(body, "path"), "content": body.get("content", ""), "append": bool(body.get("append"))},
                _ctx(runtime),
            )
        )

    def edit_file(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        return _result(
            runtime.registry.execute(
                "edit_file",
                {
                    "path": _body_str(body, "path"),
                    "old_string": _body_str(body, "old_string"),
                    "new_string": body.get("new_string", ""),
                    "replace_all": bool(body.get("replace_all")),
                },
                _ctx(runtime),
            )
        )

    def list_dir(req: Request, params: dict[str, str]) -> Response:
        return _result(
            runtime.registry.execute(
                "list_dir",
                {"path": req.q("path", "."), "recursive": req.q_bool("recursive"), "max_depth": req.q_int("max_depth", 3, lo=1, hi=6)},
                _ctx(runtime),
            )
        )

    def search(req: Request, params: dict[str, str]) -> Response:
        return _result(
            runtime.registry.execute(
                "grep_files",
                {"pattern": req.q("pattern", ""), "path": req.q("path", "."), "glob": req.q("glob", "*")},
                _ctx(runtime),
            )
        )

    def execute_python(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        code = body.get("code")
        if not isinstance(code, str) or not code.strip():
            raise AppError("code 不能为空")
        return _result(
            runtime.registry.execute("run_python", {"code": code, "timeout": body.get("timeout")}, _ctx(runtime))
        )

    router.add("GET", "/api/files", read_file)
    router.add("POST", "/api/files", write_file)
    router.add("POST", "/api/files/edit", edit_file)
    router.add("GET", "/api/dir", list_dir)
    router.add("GET", "/api/search", search)
    router.add("POST", "/api/execute/python", execute_python)

    # ---------------- Cookie ----------------
    def list_cookies(req: Request, params: dict[str, str]) -> Response:
        return json_response(runtime.cookie_store.get(req.q("domain")))

    def set_cookies(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        domain = _body_str(body, "domain")
        if "cookie" not in body:
            raise AppError("cookie 不能为空")
        summary = runtime.cookie_store.set(domain, body["cookie"], merge=bool(body.get("merge", True)))
        return json_response({"ok": True, **summary})

    def delete_cookies(req: Request, params: dict[str, str]) -> Response:
        return json_response({"ok": True, **runtime.cookie_store.delete(params["domain"])})

    def cookie_header(req: Request, params: dict[str, str]) -> Response:
        domain = params["domain"]
        header = runtime.cookie_store.to_header(domain)
        if not header:
            raise NotFoundError(f"{domain} 没有可用 Cookie")
        return json_response({"domain": domain, "cookie_header": header})

    router.add("GET", "/api/cookies", list_cookies)
    router.add("POST", "/api/cookies", set_cookies)
    router.add("DELETE", "/api/cookies/{domain}", delete_cookies)
    router.add("GET", "/api/cookies/{domain}/header", cookie_header)

    # ---------------- Skill ----------------
    def list_skills(req: Request, params: dict[str, str]) -> Response:
        if req.q_bool("refresh", True):
            runtime.skills.refresh()
        return json_response({"skills": runtime.skills.list(), "roots": [str(r) for r in runtime.settings.skills_dirs]})

    def read_skill(req: Request, params: dict[str, str]) -> Response:
        info = runtime.skills.get(params["name"])
        return json_response({"skill": info.to_dict(), "content": runtime.skills.read(params["name"])})

    router.add("GET", "/api/skills", list_skills)
    router.add("GET", "/api/skills/{name}", read_skill)

    # ---------------- 工作流 ----------------
    def list_workflows(req: Request, params: dict[str, str]) -> Response:
        return json_response({"workflows": runtime.workflows.list(), "directory": str(runtime.settings.workflows_dir)})

    def run_workflow(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        inputs = body.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise AppError("inputs 必须是对象")
        ctx = _ctx(runtime)
        session_id = body.get("session_id")
        if session_id:
            ctx = ToolContext(session=runtime.sessions.get(str(session_id)), agent_id="workflow", depth=0, allow_spawn=False)
        return json_response(runtime.workflows.run(params["name"], inputs, ctx))

    router.add("GET", "/api/workflows", list_workflows)
    router.add("POST", "/api/workflows/{name}/run", run_workflow)

    # ---------------- 子 Agent ----------------
    def list_subagents(req: Request, params: dict[str, str]) -> Response:
        return json_response(
            {
                "active": runtime.subagents.active_count,
                "max_concurrent": runtime.subagents.max_concurrent,
                "max_depth": runtime.subagents.max_depth,
                "subagents": runtime.subagents.list(),
            }
        )

    def spawn_subagent(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        task = _body_str(body, "task")
        parent_id = body.get("parent_session_id")
        parent = runtime.sessions.get(str(parent_id)) if parent_id else runtime.sessions.create(title="子 Agent 容器会话")
        system_prompt = body.get("system_prompt")
        record = runtime.subagents.spawn(
            parent=parent,
            task=task,
            system_prompt=system_prompt if isinstance(system_prompt, str) else None,
            title=str(body.get("title") or ""),
            depth=1,
        )
        return json_response(
            {
                **record.to_dict(include_result=False),
                "concurrency": f"{runtime.subagents.active_count}/{runtime.subagents.max_concurrent}",
            }
        )

    def get_subagent(req: Request, params: dict[str, str]) -> Response:
        record = runtime.subagents.get(params["sub_id"])
        return json_response(record.to_dict(include_result=True, include_trace=req.q_bool("include_trace")))

    def wait_subagent(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        timeout = float(body.get("timeout") or 300)
        record = runtime.subagents.wait(params["sub_id"], timeout=timeout)
        return json_response(
            {
                **record.to_dict(include_result=True),
                "concurrency": f"{runtime.subagents.active_count}/{runtime.subagents.max_concurrent}",
            }
        )

    def cancel_subagent(req: Request, params: dict[str, str]) -> Response:
        record: SubagentRecord = runtime.subagents.cancel(params["sub_id"])
        return json_response(record.to_dict(include_result=True))

    router.add("GET", "/api/subagents", list_subagents)
    router.add("POST", "/api/subagents", spawn_subagent)
    router.add("GET", "/api/subagents/{sub_id}", get_subagent)
    router.add("POST", "/api/subagents/{sub_id}/wait", wait_subagent)
    router.add("POST", "/api/subagents/{sub_id}/cancel", cancel_subagent)

    # ---------------- 定时任务 ----------------
    def list_schedules(req: Request, params: dict[str, str]) -> Response:
        tasks = runtime.schedules.list()
        return json_response({"ok": True, "tasks": tasks, "count": len(tasks)})

    def create_schedule(req: Request, params: dict[str, str]) -> Response:
        task = runtime.schedules.create(req.json())
        return json_response({"ok": True, "task": task})

    def get_schedule(req: Request, params: dict[str, str]) -> Response:
        return json_response({"ok": True, "task": runtime.schedules.get(params["task_id"])})

    def update_schedule(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        if body.get("enabled") is not None and len(body) == 1:
            task = runtime.schedules.set_enabled(params["task_id"], bool(body["enabled"]))
            return json_response({"ok": True, "task": task})
        return json_response({"ok": True, "task": runtime.schedules.update(params["task_id"], body)})

    def delete_schedule(req: Request, params: dict[str, str]) -> Response:
        return json_response(runtime.schedules.delete(params["task_id"]))

    def run_schedule(req: Request, params: dict[str, str]) -> Response:
        body = req.json() if req.body else {}
        wait = bool(body.get("wait"))
        return json_response(runtime.schedules.run_now(params["task_id"], wait=wait))

    def export_session(req: Request, params: dict[str, str]) -> Response:
        session = runtime.sessions.get(params["session_id"])
        lines = [f"# {session.title or '未命名会话'}", "", f"> session_id: `{session.id}`", ""]
        for message in session.messages:
            role = str(message.get("role") or "")
            if role == "system":
                continue
            content = message.get("content")
            if isinstance(content, list):
                texts = [str(part.get("text") or "") for part in content if isinstance(part, dict)]
                content = "\n".join(t for t in texts if t)
            label = {"user": "你", "assistant": "Agent", "tool": "工具"}.get(role, role)
            lines.append(f"## {label}")
            lines.append("")
            lines.append(str(content or "").strip())
            lines.append("")
        markdown = "\n".join(lines).strip() + "\n"
        return Response(
            200,
            markdown,
            headers={"Content-Disposition": f'attachment; filename="{params["session_id"]}.md"'},
            content_type="text/markdown; charset=utf-8",
        )

    router.add("GET", "/api/schedules", list_schedules)
    router.add("POST", "/api/schedules", create_schedule)
    router.add("GET", "/api/schedules/{task_id}", get_schedule)
    router.add("POST", "/api/schedules/{task_id}", update_schedule)
    router.add("DELETE", "/api/schedules/{task_id}", delete_schedule)
    router.add("POST", "/api/schedules/{task_id}/run", run_schedule)
    router.add("GET", "/api/sessions/{session_id}/export", export_session)

    from .auth_api import mount_auth

    mount_auth(router, runtime)

    return router
