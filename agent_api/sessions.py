"""会话存储 + 子 Agent 调度（线程实现，默认最多 3 个并发）。"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import AgentCancelled, ConflictError, NotFoundError, ToolError
from .utils import new_id, now_iso

logger = logging.getLogger("agent.sessions")

TERMINAL = {"done", "failed", "cancelled"}
_EMPTY_TITLES = {"", "未命名会话", "新会话"}


def _guess_title(messages: list[dict[str, Any]]) -> str:
    """用第一句用户话提炼一个短标题（纯本地规则，不花模型额度）。"""
    for msg in messages or []:
        if msg.get("role") != "user":
            continue
        text = " ".join(str(msg.get("content") or "").split())
        if not text:
            continue
        for prefix in ("请帮我", "帮我", "麻烦你", "麻烦", "请你", "请", "能不能", "可以帮我", "可以"):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip("，,、 ")
        text = text.split("。")[0].strip()
        return text[:20]
    return ""


@dataclass
class Session:
    id: str
    title: str = ""
    system_prompt: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    loaded_skills: list[str] = field(default_factory=list)
    kind: str = "main"
    parent_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    max_turns: int | None = None
    persist: bool = True
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self, include_messages: bool = True) -> dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "system_prompt": self.system_prompt,
            "system_prompt_chars": len(self.system_prompt or ""),
            "loaded_skills": self.loaded_skills,
            "kind": self.kind,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "max_turns": self.max_turns,
            "message_count": len(self.messages),
            "usage": self.usage,
        }
        if include_messages:
            data["messages"] = self.messages
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            system_prompt=data.get("system_prompt", ""),
            messages=data.get("messages", []),
            loaded_skills=data.get("loaded_skills", []),
            kind=data.get("kind", "main"),
            parent_id=data.get("parent_id"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            max_turns=data.get("max_turns"),
            usage=data.get("usage") or {},
            persist=data.get("persist", True),
        )

    def append_system_prompt(self, text: str, mode: str = "replace") -> None:
        if mode == "append":
            self.system_prompt = (self.system_prompt or "").rstrip() + "\n\n" + text.strip()
        else:
            self.system_prompt = text
        self.updated_at = now_iso()


class SessionStore:
    """会话落盘到 data/sessions/<id>.json，重启后可继续。"""

    def __init__(self, settings: Settings, default_system_prompt: str):
        self.settings = settings
        self.default_system_prompt = default_system_prompt
        self._dir: Path = settings.data_dir / "sessions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}
        self._lock = threading.RLock()

    def _file(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _load_from_disk(self, session_id: str) -> Session | None:
        path = self._file(session_id)
        if not path.exists():
            return None
        try:
            return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            logger.warning("会话文件损坏 %s: %s", path, exc)
            return None

    def create(
        self,
        title: str = "",
        system_prompt: str | None = None,
        kind: str = "main",
        parent_id: str | None = None,
        session_id: str | None = None,
    ) -> Session:
        sid = session_id or new_id("sess")
        if self._file(sid).exists():
            raise ConflictError(f"会话已存在：{sid}")
        ts = now_iso()
        session = Session(
            id=sid,
            title=title or "未命名会话",
            system_prompt=system_prompt or self.default_system_prompt,
            kind=kind,
            parent_id=parent_id,
            created_at=ts,
            updated_at=ts,
        )
        with self._lock:
            self._cache[sid] = session
            self._write(session)
        return session

    def _write(self, session: Session) -> None:
        path = self._file(session.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(session), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def save(self, session: Session) -> None:
        if not session.persist:
            return
        if session.title in _EMPTY_TITLES:
            guessed = _guess_title(session.messages)
            if guessed:
                session.title = guessed
        session.updated_at = now_iso()
        with self._lock:
            self._cache[session.id] = session
            self._write(session)

    def get(self, session_id: str) -> Session:
        with self._lock:
            if session_id in self._cache:
                return self._cache[session_id]
            session = self._load_from_disk(session_id)
            if session is None:
                raise NotFoundError(f"会话不存在：{session_id}")
            self._cache[session_id] = session
            return session

    def get_or_create(self, session_id: str | None, title: str = "", system_prompt: str | None = None) -> Session:
        if session_id:
            return self.get(session_id)
        return self.create(title=title, system_prompt=system_prompt)

    def list(self, kind: str | None = None) -> list[dict[str, Any]]:
        items: list[Session] = []
        for path in self._dir.glob("*.json"):
            try:
                session = Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, KeyError):
                continue
            if kind and session.kind != kind:
                continue
            if session.title in _EMPTY_TITLES:
                guessed = _guess_title(session.messages)
                if guessed:
                    session.title = guessed
                    try:
                        self._write(session)
                        self._cache[session.id] = session
                    except OSError:
                        pass
            items.append(session)
        items.sort(key=lambda s: s.updated_at, reverse=True)
        return [s.to_dict(include_messages=False) for s in items]

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._cache.pop(session_id, None)
            path = self._file(session_id)
            if not path.exists():
                raise NotFoundError(f"会话不存在：{session_id}")
            path.unlink()


@dataclass
class SubagentRecord:
    id: str
    parent_session_id: str
    task: str
    depth: int = 1
    title: str = ""
    status: str = "running"  # running | done | failed | cancelled
    result: str = ""
    error: str = ""
    created_at: str = ""
    finished_at: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    thread: Any = None
    cancel_event: Any = None

    def to_dict(self, include_result: bool = True, include_trace: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "parent_session_id": self.parent_session_id,
            "title": self.title,
            "task": self.task,
            "depth": self.depth,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "trace_steps": len(self.trace),
        }
        if include_result:
            data["result"] = self.result
            data["error"] = self.error
        if include_trace:
            data["trace"] = self.trace
        return data


class SubagentManager:
    """并发上限由 AGENT_MAX_SUBAGENTS 控制（默认 3），超出直接拒绝而不是排队。"""

    def __init__(self, runtime: Any, max_concurrent: int = 3, max_depth: int = 1):
        self.runtime = runtime
        self.max_concurrent = max(1, max_concurrent)
        self.max_depth = max(1, max_depth)
        self._records: dict[str, SubagentRecord] = {}
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active_count(self) -> int:
        return self._active

    def get(self, sub_id: str) -> SubagentRecord:
        with self._lock:
            record = self._records.get(sub_id)
        if record is None:
            raise NotFoundError(f"子 Agent 不存在：{sub_id}")
        return record

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            records = sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)
        return [r.to_dict(include_result=False) for r in records]

    def spawn(
        self,
        parent: Session,
        task: str,
        system_prompt: str | None = None,
        title: str = "",
        depth: int = 1,
    ) -> SubagentRecord:
        if not task.strip():
            raise ToolError("task 不能为空")
        if depth > self.max_depth:
            raise ToolError(f"子 Agent 最多嵌套 {self.max_depth} 层，当前 {depth} 层已超限")
        with self._lock:
            if self._active >= self.max_concurrent:
                raise ConflictError(
                    f"最多同时运行 {self.max_concurrent} 个子 Agent，当前已满。"
                    "请先用 wait_agent / list_agents 让已有子任务结束，或取消不需要的。"
                )
            self._active += 1
            record = SubagentRecord(
                id=new_id("sub"),
                parent_session_id=parent.id,
                task=task.strip(),
                depth=depth,
                title=title or task.strip()[:40],
                created_at=now_iso(),
                cancel_event=threading.Event(),
            )
            self._records[record.id] = record

        thread = threading.Thread(
            target=self._run,
            args=(record, parent, system_prompt),
            name=f"subagent-{record.id}",
            daemon=True,
        )
        record.thread = thread
        thread.start()
        return record

    def _run(self, record: SubagentRecord, parent: Session, custom_prompt: str | None) -> None:
        from .agent import Agent
        from .tools.base import ToolContext

        try:
            system = self._compose_prompt(record, custom_prompt)
            session = self.runtime.sessions.create(
                title=f"[子任务] {record.title}",
                system_prompt=system,
                kind="subagent",
                parent_id=parent.id,
                session_id=record.id,
            )
            session.loaded_skills = list(parent.loaded_skills)
            ctx = ToolContext(
                session=session,
                agent_id=record.id,
                depth=record.depth,
                allow_spawn=record.depth < self.max_depth,
                parent_session_id=parent.id,
            )

            def on_event(event: dict[str, Any]) -> None:
                record.trace.append(event)
                if len(record.trace) > 400:
                    record.trace = record.trace[-400:]

            agent = Agent(self.runtime, session, ctx, cancel_event=record.cancel_event)
            result = agent.run(record.task, emit=on_event)
            record.result = result.content
            record.status = "done"
        except AgentCancelled:
            record.status = "cancelled"
            record.error = "已取消"
        except Exception as exc:  # noqa: BLE001
            logger.exception("子 Agent 执行失败 %s", record.id)
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"
        finally:
            record.finished_at = now_iso()
            with self._lock:
                self._active -= 1

    def _compose_prompt(self, record: SubagentRecord, custom_prompt: str | None) -> str:
        base = self.runtime.default_system_prompt
        role = custom_prompt.strip() if custom_prompt else (
            "你是被主 Agent 派出的子 Agent，只负责下面这一个子任务。"
            "直接动手做事并给出结论，不要反问，不要等待用户确认。"
        )
        extra = "" if record.depth < self.max_depth else "\n\n注意：你不能再派发子 Agent，请自己完成全部工作。"
        return f"{base}\n\n# 你的角色\n{role}{extra}\n\n# 你负责的子任务\n{record.task}"

    def wait(self, sub_id: str, timeout: float = 300.0) -> SubagentRecord:
        record = self.get(sub_id)
        deadline = time.monotonic() + max(0.0, min(timeout, 600.0))
        while record.status not in TERMINAL and time.monotonic() < deadline:
            time.sleep(0.1)
        return record

    def cancel(self, sub_id: str) -> SubagentRecord:
        record = self.get(sub_id)
        if record.status in TERMINAL:
            return record
        if record.cancel_event is not None:
            record.cancel_event.set()
        record.error = record.error or "取消请求已发出，等待子 Agent 落停"
        return record
