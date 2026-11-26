"""OpenAI 兼容层：让 ChatBox / Cherry Studio / RikkaHub 这类客户端直接连上来。

默认是**纯模型直通**：messages / tools / 采样参数原样转给上游，不注入系统提示词、
不塞服务端工具、不压成 Agent 上下文。要 Agent 工具模式，显式加 X-Agent-Mode: agent。
请求还是要过账号与订阅闸门：Authorization: Bearer ak_… / X-API-Token。
"""

from __future__ import annotations

import contextvars
import json
import queue
import threading
import time
from dataclasses import replace
from typing import Any, Iterable

from .agent import Agent
from .deploy import is_merchant
from .errors import AppError
from .llm import build_llm
from .providers import activate, load as load_providers
from .utils import new_id
from .server import Request, Response, Router, StreamResponse
from .tools.base import ToolContext


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in ("text", "input_text"):
                parts.append(str(item.get("text") or ""))
        return "\n".join(x for x in parts if x)
    return str(content or "")


def _messages_to_prompt(messages: Any) -> str:
    if not isinstance(messages, list) or not messages:
        raise AppError("messages 不能为空")
    system, history, last_user = [], [], ""
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        text = _text(item.get("content"))
        if not text:
            continue
        if role == "system":
            system.append(text)
        elif role == "user":
            if last_user:
                history.append(("用户", last_user))
            last_user = text
        elif role == "assistant":
            if last_user:
                history.append(("用户", last_user))
                last_user = ""
            history.append(("助手", text))
    if not last_user:
        raise AppError("最后一条必须是 user 消息")
    blocks = []
    if system:
        blocks.append("【客户端系统指令】\n" + "\n".join(system))
    if history:
        lines = [f"{who}：{text}" for who, text in history[-20:]]
        blocks.append("【前面的对话】\n" + "\n".join(lines))
    blocks.append("【当前问题】\n" + last_user)
    return "\n\n".join(blocks)[:120000]


def _models(runtime: Any) -> list[str]:
    data = load_providers(runtime.settings)
    out: list[str] = []
    for provider in data.get("providers") or []:
        for model in provider.get("models") or []:
            if model and model not in out:
                out.append(str(model))
    env_model = str(getattr(runtime.settings, "llm_model", "") or "")
    if env_model and env_model not in out:
        out.append(env_model)
    active = (data.get("active") or {}).get("model") or ""
    if active in out:
        out.remove(active)
        out.insert(0, active)
    return out


def _pick_model(runtime: Any, requested: str) -> str:
    model = requested.strip()
    if not model:
        data = load_providers(runtime.settings)
        return str((data.get("active") or {}).get("model") or getattr(runtime.settings, "llm_model", "") or "")
    data = load_providers(runtime.settings)
    for provider in data.get("providers") or []:
        if model in (provider.get("models") or []):
            try:
                activate(runtime.settings, str(provider.get("id") or ""), model)
            except Exception:  # noqa: BLE001 - 切模型失败不该挡住这次请求
                pass
            break
    return model


def _ident(runtime: Any, request: Request) -> dict[str, Any]:
    auth = getattr(runtime, "auth", None)
    if auth is None or not auth.enabled():
        return {"user_id": "", "role": "admin"}
    return auth.require(request) or {}


def _gate(runtime: Any, ident: dict[str, Any]) -> dict[str, Any]:
    if not is_merchant() or getattr(runtime, "billing", None) is None:
        return {}
    if str(ident.get("role") or "") != "user":
        return {}
    return runtime.billing.require(str(ident.get("user_id") or ""))


def _consume(runtime: Any, ident: dict[str, Any], usage: dict[str, Any] | None) -> None:
    if not is_merchant() or getattr(runtime, "billing", None) is None:
        return
    if str(ident.get("role") or "") != "user":
        return
    try:
        runtime.billing.consume(str(ident.get("user_id") or ""), int((usage or {}).get("total") or 0), 1)
    except Exception:  # noqa: BLE001
        pass


def _owner(ident: dict[str, Any]) -> str:
    role = str(ident.get("role") or "")
    return str(ident.get("user_id") or "") if role == "user" and is_merchant() else ""


def _ctx(runtime: Any, session: Any, ident: dict[str, Any], account: dict[str, Any]) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id="openai",
        depth=0,
        allow_spawn=True,
        extra={
            "guest": False,
            "guest_unlocked": False,
            # API 端的工作目录由客户端自己负责，不套网页端的 GB 限额。
            "quota_mb": 0,
            "api_client": True,
            "merchant_member": is_merchant() and str(ident.get("role") or "") == "user",
        },
    )


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    prompt = int((usage or {}).get("prompt") or 0)
    completion = int((usage or {}).get("completion") or 0)
    total = int((usage or {}).get("total") or (prompt + completion))
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}




def _estimate_usage(messages: Any, content: str) -> dict[str, int]:
    """上游没回 usage 时，按字符估一个，至少让免费版扣 1 分，不能白嫖。"""
    chars = 0
    if isinstance(messages, list):
        for item in messages:
            try:
                chars += len(json.dumps(item, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                chars += len(str(item))
    prompt = max(1, chars // 4)
    completion = max(0, len(content or "") // 4)
    return {"prompt": prompt, "completion": completion, "total": prompt + completion}


def _client_for(runtime: Any, requested: str) -> tuple[Any, str]:
    """按客户端传的 model 找对应提供商；找不到就用当前模型，不改全局设置。"""
    st = runtime.settings
    model = (requested or "").strip()
    current = str(getattr(st, "llm_model", "") or "")
    if not model or model == current:
        return runtime.llm, current or model
    try:
        data = load_providers(st)
    except Exception:  # noqa: BLE001
        return runtime.llm, current or model
    for provider in data.get("providers") or []:
        if model not in (provider.get("models") or []):
            continue
        key = str(provider.get("api_key") or getattr(st, "llm_api_key", "") or "")
        new = replace(
            st,
            llm_base_url=str(provider.get("base_url") or st.llm_base_url).rstrip("/"),
            llm_api_key=key,
            llm_model=model,
            llm_mock="false" if key else "auto",
        )
        try:
            return build_llm(new), model
        except Exception:  # noqa: BLE001
            return runtime.llm, current or model
    return runtime.llm, current or model


def _agent_mode(request: Request, body: dict[str, Any]) -> bool:
    """默认是纯模型直通；要 Agent 工具/系统提示词，显式加 X-Agent-Mode: agent。"""
    header = str(request.header("x-agent-mode") or "").strip().lower()
    if header in {"agent", "tools"}:
        return True
    return str(body.get("agent_mode") or "").strip().lower() in {"agent", "tools"} or body.get("agent") is True


def _message_obj(result: dict[str, Any], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    content = result.get("content")
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": (content if content is not None else (None if tool_calls else "")),
    }
    reasoning = result.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        msg["reasoning_content"] = reasoning
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_delta(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for index, call in enumerate(tool_calls):
        fn = call.get("function") or {}
        out.append({
            "index": index,
            "id": call.get("id") or new_id("call"),
            "type": "function",
            "function": {"name": fn.get("name") or "", "arguments": fn.get("arguments") or "{}"},
        })
    return out


def mount(router: Router, runtime: Any) -> None:
    def list_models(request: Request, params: dict[str, str]) -> Response:
        _ident(runtime, request)
        data = [{"id": m, "object": "model", "created": int(time.time()), "owned_by": "agent-api"}
                for m in _models(runtime)]
        return Response(200, {"object": "list", "data": data})

    def raw_chat(request: Request, body: dict[str, Any], ident: dict[str, Any], account: dict[str, Any],
                 stream: bool) -> Response | StreamResponse:
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise AppError("messages 不能为空")
        if not all(isinstance(item, dict) for item in messages):
            raise AppError("messages 每项都必须是对象")
        tools = body.get("tools") if isinstance(body.get("tools"), list) and body.get("tools") else None
        temperature = body.get("temperature") if isinstance(body.get("temperature"), (int, float)) else None
        max_tokens = body.get("max_tokens")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            max_tokens = None

        extra = {k: body[k] for k in (
            "top_p", "top_k", "stop", "presence_penalty", "frequency_penalty",
            "seed", "response_format", "logit_bias", "user",
            "tool_choice", "parallel_tool_calls", "max_completion_tokens",
        ) if k in body}
        requested = str(body.get("model") or "")
        llm, model = _client_for(runtime, requested)
        created = int(time.time())
        cid = "chatcmpl-" + new_id("raw").replace("raw_", "")

        if not stream:
            result = llm.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens, extra=extra)
            content = str(result.get("content") or "")
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
            if not usage:
                usage = _estimate_usage(messages, content)
            _consume(runtime, ident, usage)
            calls = result.get("tool_calls") or []
            return Response(200, {
                "id": cid, "object": "chat.completion", "created": created,
                "model": model or requested,
                "choices": [{
                    "index": 0,
                    "message": _message_obj(result, calls),
                    "finish_reason": "tool_calls" if calls else "stop",
                }],
                "usage": _usage(usage),
            })

        events: queue.Queue = queue.Queue()
        sentinel = object()
        root_context = contextvars.copy_context()

        def worker() -> None:
            try:
                result = llm.stream_chat(
                    messages, tools=tools, on_event=events.put,
                    temperature=temperature, max_tokens=max_tokens, extra=extra,
                )
                content = str(result.get("content") or "")
                usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
                if not usage:
                    usage = _estimate_usage(messages, content)
                _consume(runtime, ident, usage)
                events.put(("final", result, dict(usage)))
            except Exception as exc:  # noqa: BLE001
                events.put(("error", str(exc), {}))
            finally:
                events.put(sentinel)

        threading.Thread(target=root_context.run, args=(worker,), name=f"openai-raw-{cid}", daemon=True).start()

        def gen() -> Iterable[str]:
            def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
                return "data: " + json.dumps({
                    "id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model or requested,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }, ensure_ascii=False) + "\n\n"

            yield chunk({"role": "assistant"})
            sent = 0
            while True:
                try:
                    event = events.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                if event is sentinel:
                    break
                kind = event[0] if isinstance(event, tuple) else event.get("type")
                if kind == "content_delta":
                    text = event[1] if isinstance(event, tuple) else event.get("text", "")
                    if text:
                        sent += len(text)
                        yield chunk({"content": text})
                elif kind == "reasoning_delta":
                    text = event[1] if isinstance(event, tuple) else event.get("text", "")
                    if text:
                        yield chunk({"reasoning_content": text})
                elif kind == "final":
                    result, usage = event[1], event[2]
                    content = str(result.get("content") or "")
                    if len(content) > sent:
                        tail = content[sent:]
                        sent = len(content)
                        yield chunk({"content": tail})
                    calls = result.get("tool_calls") or []
                    if calls:
                        yield chunk({"tool_calls": _tool_delta(calls)})
                    final = {
                        "id": cid, "object": "chat.completion.chunk", "created": created,
                        "model": model or requested,
                        "choices": [{"index": 0, "delta": {},
                                     "finish_reason": "tool_calls" if calls else "stop"}],
                        "usage": _usage(usage),
                    }
                    yield "data: " + json.dumps(final, ensure_ascii=False) + "\n\n"
                elif kind == "error":
                    yield "data: " + json.dumps(
                        {"error": {"message": event[1], "type": "upstream_error"}},
                        ensure_ascii=False,
                    ) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamResponse(gen())

    def agent_chat(request: Request, body: dict[str, Any], ident: dict[str, Any],
                   account: dict[str, Any], stream: bool) -> Response | StreamResponse:
        prompt = _messages_to_prompt(body.get("messages"))
        requested = str(body.get("model") or "")
        model = _pick_model(runtime, requested)
        owner = _owner(ident)
        session = runtime.sessions.create(
            title=str(body.get("user") or "外部客户端")[:60] or "外部客户端",
            owner=owner,
        )
        agent = Agent(runtime, session, _ctx(runtime, session, ident, account))
        created = int(time.time())
        cid = "chatcmpl-" + session.id.replace("sess_", "")

        if not stream:
            result = agent.run(prompt)
            _consume(runtime, ident, session.usage)
            return Response(200, {
                "id": cid, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": result.content},
                             "finish_reason": "stop"}],
                "usage": _usage(session.usage),
            })

        events: queue.Queue = queue.Queue()
        sentinel = object()
        root_context = contextvars.copy_context()

        def worker() -> None:
            try:
                result = agent.run(prompt, emit=events.put)
                _consume(runtime, ident, session.usage)
                events.put(("final", result.content, dict(session.usage)))
            except Exception as exc:  # noqa: BLE001
                events.put(("error", str(exc), {}))
            finally:
                events.put(sentinel)

        threading.Thread(target=root_context.run, args=(worker,), name=f"openai-{session.id}", daemon=True).start()

        def gen() -> Iterable[str]:
            yield "data: " + json.dumps({
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }, ensure_ascii=False) + "\n\n"
            sent = False
            while True:
                try:
                    event = events.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                if event is sentinel:
                    break
                kind = event[0] if isinstance(event, tuple) else event.get("type")
                if kind == "content_delta":
                    text = event[1] if isinstance(event, tuple) else event.get("text", "")
                    if text:
                        sent = True
                        yield "data: " + json.dumps({
                            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                        }, ensure_ascii=False) + "\n\n"
                elif kind == "final":
                    text, usage = event[1], event[2]
                    if text and not sent:
                        yield "data: " + json.dumps({
                            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                        }, ensure_ascii=False) + "\n\n"
                    yield "data: " + json.dumps({
                        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": _usage(usage),
                    }, ensure_ascii=False) + "\n\n"
                elif kind == "error":
                    yield "data: " + json.dumps({"error": {"message": event[1], "type": "agent_error"}}, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamResponse(gen())

    def chat(request: Request, params: dict[str, str]) -> Response | StreamResponse:
        body = request.json()
        ident = _ident(runtime, request)
        account = _gate(runtime, ident)
        stream = bool(body.get("stream"))
        if _agent_mode(request, body):
            return agent_chat(request, body, ident, account, stream)
        return raw_chat(request, body, ident, account, stream)

    router.add("GET", "/v1/models", list_models, auth=True)
    router.add("POST", "/v1/chat/completions", chat, auth=True)
    router.add("POST", "/v1/chat/completions/", chat, auth=True)
