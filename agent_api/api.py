"""全部 HTTP 接口实现（路由表装配在这里）。"""

from __future__ import annotations

import json
import logging
import contextvars
import os
import queue
import threading
from typing import Any

from .deploy import is_merchant
from .security import active_root
from . import documents
from .agent import Agent
from .errors import AppError, NotFoundError
from .sessions import Session, SubagentRecord
from .server import Request, Response, Router, StreamResponse, json_response
from .tools.base import ToolContext

from pathlib import Path


logger = logging.getLogger("agent.api")

SSE_PING_SECONDS = float(os.getenv("SSE_PING_SECONDS") or 10)


def _ident(req: Request) -> dict[str, Any]:
    return dict(getattr(req, "identity", None) or {})


def _is_guest(req: Request) -> bool:
    return str(_ident(req).get("role") or "") == "guest"


def _owner(req: Request) -> str:
    ident = _ident(req)
    role = str(ident.get("role") or "")
    if role == "guest" or (role == "user" and is_merchant()):
        return str(ident.get("user_id") or "")
    return ""


def _billing_account(runtime: Any, req: Request) -> dict[str, Any]:
    if not is_merchant() or getattr(runtime, "billing", None) is None:
        return {}
    ident = _ident(req)
    if str(ident.get("role") or "") != "user":
        return {}
    return runtime.billing.require(str(ident.get("user_id") or ""))


def _billing_consume(runtime: Any, req: Request, usage: dict[str, Any] | None) -> None:
    if not is_merchant() or getattr(runtime, "billing", None) is None:
        return
    ident = _ident(req)
    if str(ident.get("role") or "") != "user":
        return
    try:
        runtime.billing.consume(str(ident.get("user_id") or ""), int((usage or {}).get("total") or 0), 1)
    except Exception:  # noqa: BLE001 - 记账失败不该把已经成功的回答吞掉
        logger.warning("订阅记账失败", exc_info=True)


def _own(session: Session, req: Request) -> Session:
    owner = _owner(req)
    if owner and session.owner != owner:
        raise NotFoundError(f"会话不存在：{session.id}")
    return session


def _guest_enter(runtime: Any, req: Request, chat: bool = False) -> dict[str, Any]:
    """游客进门三道闸：有效期 / 一分钟一条 / 100MB 空间。返回游客状态。"""
    ident = _ident(req)
    if str(ident.get("role") or "") != "guest":
        return {}
    uid = str(ident.get("user_id") or "")
    state = runtime.auth.guest_check(uid, chat=chat)
    if int(state.get("used_bytes") or 0) >= int(state.get("quota_bytes") or 0):
        raise AppError(f"游客空间 {state.get('quota_mb')}MB 已经用满：删点自己的文件，或注册正式账号")
    return state


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


def _ctx(runtime: Any, session: Session | None = None, req: Request | None = None) -> ToolContext:
    ident = dict(getattr(req, "identity", None) or {}) if req is not None else {}
    guest = str(ident.get("role") or "") == "guest"
    unlocked = False
    quota_mb = 0
    if guest:
        try:
            state = runtime.auth.guest_state(str(ident.get("user_id")))
            unlocked = bool(state.get("unlocked"))
            quota_mb = int(state.get("quota_mb") or 0)
        except Exception:  # noqa: BLE001
            unlocked = False
    elif is_merchant() and str(ident.get("role") or "") == "user":
        try:
            quota_mb = int(runtime.billing.account(str(ident.get("user_id"))).get("storage_mb") or 0)
        except Exception:  # noqa: BLE001
            quota_mb = 0
    return ToolContext(
        session=session or Session(id="api-direct", title="API 直调", persist=False),
        agent_id="api",
        allow_spawn=False,
        extra={
            "guest": guest, "guest_unlocked": unlocked, "quota_mb": quota_mb,
            "merchant_member": is_merchant() and str(ident.get("role") or "") == "user",
        },
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
        role = str(_ident(req).get("role") or "")
        restricted = role == "guest" or (role == "user" and is_merchant())
        ctx = _ctx(runtime, req=req) if restricted else None
        return json_response({"tools": runtime.registry.describe(ctx)})

    router.add("GET", "/health", health, auth=False)
    router.add("GET", "/api/config", config)
    router.add("GET", "/api/tools", tools)

    # ---------------- 对话 ----------------
    def _prepare_session(body: dict[str, Any], owner: str = "") -> Session:
        session_id = body.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise AppError("session_id 必须是字符串")
        title = str(body.get("title") or "")
        prompt = body.get("system_prompt") if isinstance(body.get("system_prompt"), str) else None
        session: Session | None = None
        if session_id:
            try:
                session = runtime.sessions.get(session_id)
            except NotFoundError:
                logger.warning("会话 %s 不存在（可能换过 data 目录），自动新建一个", session_id)
                session = None
            if session is not None:
                if owner and session.owner != owner:
                    raise NotFoundError(f"会话不存在：{session.id}")
                if owner and not session.owner:
                    session.owner = owner
            else:
                session = runtime.sessions.create(title=title, system_prompt=prompt, owner=owner)
        else:
            session = runtime.sessions.create(title=title, system_prompt=prompt, owner=owner)
        if session_id:
            if body.get("title"):
                session.title = str(body["title"])
            if prompt and prompt.strip():
                session.append_system_prompt(prompt, mode="replace")
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

    def _expand_documents(atts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """PDF / Word 附件：自动抽文字 + 前几页渲染成图，跟图片走同一条挂载通道。"""
        if not atts:
            return atts, []
        notes: list[str] = []
        out: list[dict[str, Any]] = []
        root = active_root()
        for item in atts:
            raw = str(item.get("path") or "")
            suffix = Path(raw).suffix.lower()
            if not documents.is_document(suffix):
                out.append(item)
                continue
            target = Path(raw)
            if not target.is_absolute():
                target = root / raw
            if not target.exists():
                notes.append(f"[附件 {raw}：服务端没找到这个文件]")
                out.append(item)
                continue
            try:
                out_dir = root / "_docpages" / (Path(raw).stem[:40] or "doc")
                expanded = documents.expand_attachment(target, out_dir, max_pages=6)
            except Exception as exc:  # noqa: BLE001 - 文档坏了不能把整轮拖垮
                logger.warning("附件展开失败 %s：%s", raw, exc)
                notes.append(f"[附件 {raw}：展开失败 {type(exc).__name__}: {exc}]")
                out.append(item)
                continue
            note = f"{expanded['text']}"
            if expanded["note"]:
                note += f"\n（{expanded['note']}）"
            notes.append(note)
            for image in expanded["images"]:
                try:
                    rel = str(Path(image).relative_to(root))
                except ValueError:
                    rel = str(image)
                out.append({"path": rel, "name": Path(image).name, "type": "docpage"})
        return out[:8], notes

    def _with_attachments(text: str, atts: list[dict[str, Any]], notes: list[str] | None = None) -> str:
        if not atts and not notes:
            return text
        parts = [text]
        labels = {"image": "图片", "docpage": "文档页图", "file": "文件"}
        if atts:
            lines = [f"- {a['path']}（{labels.get(str(a.get('type') or ''), '文件')}）" for a in atts]
            parts.append("[附件（都在 workspace 下）]\n" + "\n".join(lines))
        if notes:
            has_pages = any(str(a.get("type")) == "docpage" for a in atts)
            head = "[文档内容已自动抽取" + ("，页面图已挂上]" if has_pages else "，只发了文字]")
            parts.append(head + "\n" + "\n\n".join(notes))
        return "\n\n".join(parts)

    def chat(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise AppError("message 不能为空")
        guest = _guest_enter(runtime, req, chat=True)
        billing = _billing_account(runtime, req)
        session = _prepare_session(body, owner=_owner(req))
        atts = _attachments(body)
        atts, doc_notes = _expand_documents(atts)
        max_turns = body.get("max_turns")
        ctx = ToolContext(
            session=session,
            agent_id="main",
            depth=0,
            allow_spawn=not _is_guest(req),
            extra={
                "guest": _is_guest(req),
                "guest_unlocked": bool(guest.get("unlocked")),
                "quota_mb": int(guest.get("quota_mb") or billing.get("storage_mb") or 0),
                "merchant_member": is_merchant() and str(_ident(req).get("role") or "") == "user",
            },
        )
        agent = Agent(runtime, session, ctx, max_turns=int(max_turns) if max_turns else None)
        result = agent.run(_with_attachments(message, atts, doc_notes), attachments=atts)
        _billing_consume(runtime, req, session.usage)
        return json_response(
            {
                "session_id": session.id,
                "content": result.content,
                "turns": result.turns,
                "steps": result.steps,
                "message_count": len(session.messages),
                "subagents_active": runtime.subagents.active_count,
                "subagents_max": runtime.subagents.max_concurrent,
                "usage": session.usage,
            }
        )

    def chat_stream(req: Request, params: dict[str, str]) -> Response | StreamResponse:
        body = req.json()
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise AppError("message 不能为空")
        guest = _guest_enter(runtime, req, chat=True)
        billing = _billing_account(runtime, req)
        session = _prepare_session(body, owner=_owner(req))
        atts = _attachments(body)
        atts, doc_notes = _expand_documents(atts)
        max_turns = body.get("max_turns")
        ctx = ToolContext(
            session=session,
            agent_id="main",
            depth=0,
            allow_spawn=not _is_guest(req),
            extra={
                "guest": _is_guest(req),
                "guest_unlocked": bool(guest.get("unlocked")),
                "quota_mb": int(guest.get("quota_mb") or billing.get("storage_mb") or 0),
                "merchant_member": is_merchant() and str(_ident(req).get("role") or "") == "user",
            },
        )
        agent = Agent(runtime, session, ctx, max_turns=int(max_turns) if max_turns else None)

        events: queue.Queue = queue.Queue()
        sentinel = object()
        root_context = contextvars.copy_context()

        def worker() -> None:
            try:
                result = agent.run(_with_attachments(message, atts, doc_notes), emit=events.put, attachments=atts)
                _billing_consume(runtime, req, session.usage)
                events.put(
                    {
                        "type": "final",
                        "session_id": session.id,
                        "content": result.content,
                        "turns": result.turns,
                        "usage": dict(session.usage),
                    }
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

        threading.Thread(target=root_context.run, args=(worker,), name=f"chat-stream-{session.id}", daemon=True).start()

        return StreamResponse(_sse_stream(events, sentinel))

    router.add("POST", "/api/chat", chat)
    router.add("POST", "/api/chat/stream", chat_stream)

    # ---------------- 会话 ----------------
    def list_sessions(req: Request, params: dict[str, str]) -> Response:
        return json_response({"sessions": runtime.sessions.list(kind=req.q("kind"), owner=_owner(req) or None)})

    def create_session(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        session = runtime.sessions.create(
            title=str(body.get("title") or ""),
            system_prompt=body.get("system_prompt") if isinstance(body.get("system_prompt"), str) else None,
            owner=_owner(req),
        )
        return json_response(session.to_dict(include_messages=False))

    def get_session(req: Request, params: dict[str, str]) -> Response:
        session = _own(runtime.sessions.get(params["session_id"]), req)
        return json_response(session.to_dict(include_messages=req.q_bool("include_messages")))

    def delete_session(req: Request, params: dict[str, str]) -> Response:
        _own(runtime.sessions.get(params["session_id"]), req)
        runtime.sessions.delete(params["session_id"])
        return json_response({"ok": True, "deleted": params["session_id"]})

    def session_messages(req: Request, params: dict[str, str]) -> Response:
        session = _own(runtime.sessions.get(params["session_id"]), req)
        limit = req.q_int("limit", 200, lo=1, hi=2000)
        return json_response(
            {
                "session_id": session.id,
                "count": len(session.messages),
                "messages": session.messages[-limit:],
                "usage": session.usage,
            }
        )

    def session_prompt(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        session = _own(runtime.sessions.get(params["session_id"]), req)
        prompt = _body_str(body, "prompt")
        mode = str(body.get("mode") or "replace").lower()
        if mode not in {"replace", "append"}:
            raise AppError("mode 只能是 replace 或 append")
        session.append_system_prompt(prompt, mode=mode)
        runtime.sessions.save(session)
        return json_response({"ok": True, "chars": len(session.system_prompt), "mode": mode})

    def session_load_skill(req: Request, params: dict[str, str]) -> Response:
        session = _own(runtime.sessions.get(params["session_id"]), req)
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
                _ctx(runtime, req=req),
            )
        )

    def write_file(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        return _result(
            runtime.registry.execute(
                "write_file",
                {"path": _body_str(body, "path"), "content": body.get("content", ""), "append": bool(body.get("append"))},
                _ctx(runtime, req=req),
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
                _ctx(runtime, req=req),
            )
        )

    def list_dir(req: Request, params: dict[str, str]) -> Response:
        return _result(
            runtime.registry.execute(
                "list_dir",
                {"path": req.q("path", "."), "recursive": req.q_bool("recursive"), "max_depth": req.q_int("max_depth", 3, lo=1, hi=6)},
                _ctx(runtime, req=req),
            )
        )

    def search(req: Request, params: dict[str, str]) -> Response:
        return _result(
            runtime.registry.execute(
                "grep_files",
                {"pattern": req.q("pattern", ""), "path": req.q("path", "."), "glob": req.q("glob", "*")},
                _ctx(runtime, req=req),
            )
        )

    def execute_python(req: Request, params: dict[str, str]) -> Response:
        body = req.json()
        code = body.get("code")
        if not isinstance(code, str) or not code.strip():
            raise AppError("code 不能为空")
        return _result(
            runtime.registry.execute("run_python", {"code": code, "timeout": body.get("timeout")}, _ctx(runtime, req=req))
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
        ctx = _ctx(runtime, req=req)
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
    from .billing_api import mount_billing

    mount_auth(router, runtime)
    mount_billing(router, runtime)

    return router
