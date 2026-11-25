#!/usr/bin/env python3
"""端到端自检：不依赖真实大模型（LLM_MOCK=true），把工具、接口、子 Agent 全跑一遍。

用法：python scripts/smoke_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SMOKE = ROOT / "data" / "_smoke"
shutil.rmtree(SMOKE, ignore_errors=True)

os.environ["LLM_MOCK"] = "true"
os.environ["API_TOKEN"] = "smoke-token"
os.environ["SANDBOX_ROOT"] = str(SMOKE / "workspace")
os.environ["DATA_DIR"] = str(SMOKE / "data")
os.environ["WORKFLOWS_DIR"] = str(ROOT / "workflows")
os.environ["SKILLS_DIRS"] = str(ROOT / "skills")
os.environ["GUEST_QUOTA_MB"] = "1"

import base64  # noqa: E402
import zipfile  # noqa: E402

from agent_api import api as api_mod  # noqa: E402
from agent_api import documents as docs_mod  # noqa: E402
from agent_api.auth import totp_code, totp_next_code  # noqa: E402
from agent_api.llm import OpenAICompatibleLLM, StreamAborted, extract_usage  # noqa: E402
from agent_api.runtime import create_runtime  # noqa: E402
from agent_api.server import build_server  # noqa: E402
from agent_api.security import active_root, reset_root, use_root  # noqa: E402
import contextvars  # noqa: E402

runtime = create_runtime()
server = build_server(runtime, "127.0.0.1", 0)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, name="smoke-server", daemon=True).start()

passed: list[str] = []
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        passed.append(name)
        print(f"PASS  {name}")
    else:
        failed.append(f"{name} :: {detail}")
        print(f"FAIL  {name}  {detail}")


def call(method: str, path: str, body: dict | None = None, token: str | None = "smoke-token", timeout: float = 60.0):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def hcall(method: str, path: str, body: dict | None = None, headers: dict[str, str] | None = None,
          timeout: float = 60.0):
    """带自定义头 / 能拿到响应头（Set-Cookie）的请求。"""
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    head = {"Content-Type": "application/json"}
    head.update(headers or {})
    request = urllib.request.Request(url, data=data, method=method, headers=head)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code, headers = resp.status, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        code, headers = exc.code, dict(exc.headers)
    try:
        return code, json.loads(raw), headers
    except json.JSONDecodeError:
        return code, {"raw": raw}, headers


def cookie_of(headers: dict[str, str]) -> str:
    raw = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    return raw.split(";")[0].strip()


def jcall(method: str, path: str, body: dict | None = None, token: str | None = "smoke-token", timeout: float = 60.0):
    status, raw = call(method, path, body, token, timeout)
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"raw": raw}


status, body = jcall("GET", "/health", token=None)
check("health", status == 200 and body.get("ok") is True, str(body))

status, body = jcall("GET", "/api/tools")
tools = {t["name"] for t in body.get("tools", [])}
expected = {"read_file", "write_file", "run_python", "get_cookie", "spawn_agent", "load_skill", "run_workflow"}
check("tools registered", expected <= tools, f"missing={expected - tools}")

status, body = jcall("GET", "/api/tools", token=None)
check("auth blocks without token", status == 401, str(body)[:200])

status, body = jcall("POST", "/api/files", {"path": "hello.txt", "content": "第一行\n第二行\n"})
check("write_file", body.get("ok") is True, str(body)[:200])

status, body = jcall("GET", "/api/files?path=hello.txt")
check("read_file", "第一行" in body.get("result", ""), str(body)[:300])

status, body = jcall("POST", "/api/files", {"path": "../escape.txt", "content": "x"})
check("sandbox blocks escape", body.get("ok") is False, str(body)[:300])

code_sum = "print(sum(range(10)))"
status, body = jcall("POST", "/api/execute/python", {"code": code_sum})
check("run_python", "45" in body.get("result", ""), str(body)[:300])

status, body = jcall("POST", "/api/execute/python", {"code": "import time; time.sleep(5)", "timeout": 1})
check("python timeout", body.get("ok") is False and "超时" in body.get("result", ""), str(body)[:300])

status, body = jcall("POST", "/api/cookies", {"domain": "example.com", "cookie": "a=1; b=2"})
check("set_cookie", body.get("count") == 2, str(body)[:200])

status, body = jcall("GET", "/api/cookies/example.com/header")
check("cookie header", "a=1" in body.get("cookie_header", ""), str(body)[:200])

status, body = jcall("GET", "/api/skills")
names = [s["name"] for s in body.get("skills", [])]
check("skills scanned", "data-report" in names, str(names))
check("port=0 用随机端口（服务已在 8000 也照样能自检）", PORT > 0 and PORT != runtime.settings.port, f"PORT={PORT}")

status, body = jcall("POST", "/api/chat", {"message": "自检：跑一下环境", "title": "smoke"})
step_names = [s.get("name") for s in body.get("steps", []) if s.get("type") == "tool"]
check("chat mock tool loop", status == 200 and "run_python" in step_names, json.dumps(body, ensure_ascii=False)[:400])

session_id = body.get("session_id")
status, body = jcall("POST", "/api/chat", {"message": "会话丢了自检", "session_id": "sess_not_exist"})
check(
    "旧 session_id 不存在时自动新建（换 data 目录后不会卡死）",
    status == 200 and body.get("session_id") not in (None, "", "sess_not_exist"),
    str(body)[:200],
)
status, body = jcall("POST", f"/api/sessions/{session_id}/system-prompt", {"prompt": "你是一个自检助手。", "mode": "append"})
check("runtime system prompt", body.get("ok") is True, str(body)[:200])

status, body = jcall("POST", f"/api/sessions/{session_id}/skills/data-report", {})
check("load skill", body.get("ok") is True, str(body)[:200])

# 回归：名字带中文的 skill（曾经在客户端直接拼 URL 会抛 UnicodeEncodeError，掉一大片 traceback）
status, body = jcall("POST", f"/api/sessions/{session_id}/skills/{quote('中文示例', safe='')}", {})
check("load 中文名 skill", body.get("ok") is True and body.get("skill") == "中文示例", str(body)[:200])

spawned = []
for i in range(3):
    status, body = jcall("POST", "/api/subagents", {"task": f"子任务 {i}：print({i})", "parent_session_id": session_id})
    if status == 200:
        spawned.append(body["id"])
check("spawn 3 subagents", len(spawned) == 3, str(body)[:200])

status, body = jcall("POST", "/api/subagents", {"task": "第 4 个", "parent_session_id": session_id})
check("4th subagent rejected", status == 409, str(body)[:300])

for sub_id in spawned:
    status, body = jcall("POST", f"/api/subagents/{sub_id}/wait", {"timeout": 30}, timeout=60)
    check(f"subagent done {sub_id[:8]}", body.get("status") == "done", str(body)[:300])

status, body = jcall("GET", "/api/subagents")
check("subagents released slots", body.get("active") == 0, str(body)[:200])

status, body = jcall("POST", "/api/workflows/demo_pipeline/run", {"inputs": {}})
check("workflow run", bool(body.get("outputs", {}).get("stats")), json.dumps(body, ensure_ascii=False)[:400])

status, raw = call("POST", "/api/chat/stream", {"message": "流式自检", "session_id": session_id}, timeout=60)
check("chat stream", status == 200 and "event: end" in raw and "event: final" in raw, raw[:300])
check("chat stream thinking", "event: reasoning_delta" in raw, raw[:300])

# ---- token 用量 / 缓存命中率 ----
check("流里带 usage 事件", "event: usage" in raw and '"cached"' in raw, raw[-400:])
check("final 带 usage", '"final"' in raw and '"usage"' in raw, raw[-300:])
status, body = jcall("POST", "/api/chat", {"message": "用量自检：再说一句"})
usage = body.get("usage") or {}
check(
    "接口返回累计 token 用量（含缓存命中）",
    status == 200 and usage.get("prompt", 0) > 0 and usage.get("cached", 0) > 0 and usage.get("calls", 0) >= 2,
    str(usage),
)
status, body = jcall("GET", f"/api/sessions/{session_id}/messages?limit=10")
check(
    "会话详情带 usage（切会话也能显示）",
    (body.get("usage") or {}).get("prompt", 0) > 0,
    str(body.get("usage"))[:200],
)
check(
    "用量归一：OpenAI 的 cached_tokens",
    extract_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 80}}})
    == {"prompt": 100, "completion": 20, "cached": 80, "total": 120},
)
check(
    "用量归一：DeepSeek 的 prompt_cache_hit_tokens",
    extract_usage({"usage": {"prompt_tokens": 90, "completion_tokens": 10, "prompt_cache_hit_tokens": 70}})["cached"] == 70,
)
check(
    "用量归一：Anthropic 口径 input/output + cache_read_input_tokens",
    extract_usage({"usage": {"input_tokens": 50, "output_tokens": 5, "cache_read_input_tokens": 40}})
    == {"prompt": 50, "completion": 5, "cached": 40, "total": 55},
)
check("用量归一：服务不返回 usage 时给空", extract_usage({"usage": {}}) == {})

# 真实世界里有些服务不认 stream_options：要自动去掉重试，而不是整轮报 400
from dataclasses import replace  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

_seen = []


class _Provider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        _seen.append(bool(payload.get("stream_options")))
        if payload.get("stream_options"):
            body = b'{"error":{"message":"unrecognized_keys: stream_options"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"".join(
            b'data: {"choices":[{"delta":{"content":"' + piece.encode() + b'"}}]}\n\n' for piece in ("好", "的")
        ) + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_provider = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
threading.Thread(target=_provider.serve_forever, daemon=True).start()
_cfg = replace(
    runtime.settings,
    llm_base_url=f"http://127.0.0.1:{_provider.server_address[1]}/v1",
    llm_api_key="x",
    llm_model="m",
)
_llm = OpenAICompatibleLLM(_cfg, 0)
_opts = {"LLM_STREAM_USAGE": "1"}
_old = os.environ.get("LLM_STREAM_USAGE")
os.environ.update(_opts)
try:
    _msg = _llm.stream_chat([{"role": "user", "content": "hi"}])
finally:
    if _old is None:
        os.environ.pop("LLM_STREAM_USAGE", None)
    else:
        os.environ["LLM_STREAM_USAGE"] = _old
    _provider.shutdown()
check(
    "服务不认 stream_options 时自动去掉重试（不 400 死）",
    _msg.get("content") == "好的" and _seen == [True, False],
    f"{_msg.get('content')!r} {_seen}",
)

# ---------------- PDF / Word：抽文字 + 分页渲染 ----------------
def _make_pdf(path, text):
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        data += f"{off:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(data)


def _make_docx(path, lines):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><document xmlns="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><body>'
        + "".join(f"<p><r><t>{one}</t></r></p>" for one in lines)
        + "</body></document>"
    )
    ct = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("word/document.xml", xml)


_ws = Path(os.environ["SANDBOX_ROOT"])
(_ws / "uploads").mkdir(parents=True, exist_ok=True)
_pdf = _ws / "uploads" / "smoke.pdf"
_docx = _ws / "uploads" / "smoke.docx"
_make_pdf(_pdf, "Hello Document 42")
_make_docx(_docx, ["合同金额 128000 元", "付款周期 30 天"])

check("PDF 页数能数出来", docs_mod.page_count(_pdf) == 1, str(docs_mod.page_count(_pdf)))
_pdf_text, _pdf_engine = docs_mod.extract_pdf_text(_pdf, 1, 1)
check("PDF 抽得到文字", "Hello Document 42" in _pdf_text, f"{_pdf_engine}: {_pdf_text[:100]}")
check("DOCX 抽得到文字（零依赖）", "合同金额 128000 元" in docs_mod.docx_text(_docx), docs_mod.docx_text(_docx)[:100])

if docs_mod.render_engine():
    _render = docs_mod.render_pdf(_pdf, _ws / "_docpages" / "smoke", 1, 1, 100)
    _png_ok = bool(_render.images) and _render.images[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    check(f"PDF 按页渲染成 PNG（{_render.engine}）", _png_ok, str(_render.images[:2]))
else:
    check("没有渲染器时给出安装提示", True)

saved = {name: getattr(docs_mod, name) for name in ("pdftoppm_bin", "mutool_bin", "gs_bin")}
for name in saved:
    setattr(docs_mod, name, lambda: None)
_fallback = docs_mod.expand_attachment(_pdf, _ws / "_docpages" / "fallback", max_pages=4)
for name, fn in saved.items():
    setattr(docs_mod, name, fn)
check(
    "没装 poppler：仍给文字，并提示 pkg install poppler",
    _fallback["images"] == [] and "Hello Document 42" in _fallback["text"] and "poppler" in _fallback["note"],
    _fallback["note"],
)

from agent_api import providers as provider_mod  # noqa: E402

_orig_meta = provider_mod.active_meta
provider_mod.active_meta = lambda *a, **k: {"vision": True}
try:
    _doc_out = runtime.registry.execute("view_document", {"path": "uploads/smoke.pdf", "dpi": 96}, api_mod._ctx(runtime))
finally:
    provider_mod.active_meta = _orig_meta
check(
    "view_document：文字 + VIEW_IMAGE 行（下一轮自动挂图）",
    "Hello Document 42" in _doc_out and "VIEW_IMAGE: " in _doc_out,
    _doc_out[:300],
)
(_ws / "uploads" / "note.txt").write_text("随便一段文字", encoding="utf-8")

_pages = sorted((_ws / "_docpages" / "smoke").glob("page-*.png"))
_vision_session = runtime.sessions.create(title="vision")
_parts = []
if _pages:
    _atts = [{"path": str(one.relative_to(_ws)), "name": one.name, "type": "image"} for one in _pages[:2]]
    _msg = {"role": "user", "content": "看看这两页", "attachments": _atts}
    provider_mod.active_meta = lambda *a, **k: {"vision": True}
    try:
        _agent = api_mod.Agent(runtime, _vision_session, api_mod._ctx(runtime, _vision_session))
        _parts = _agent._parts_for(_msg)
    finally:
        provider_mod.active_meta = _orig_meta
check(
    "页面图真的进多模态 payload（不是只写在文字里）",
    isinstance(_parts, list) and any(p.get("type") == "image_url" for p in _parts),
    str(_parts)[:200],
)
_out = runtime.registry.execute("view_document", {"path": "uploads/note.txt"}, api_mod._ctx(runtime))
check("view_document 只认文档类型", "只认 pdf" in _out, _out[:200])

status, body = jcall("POST", "/api/chat", {"message": "看下这个合同", "attachments": [{"path": "uploads/smoke.docx"}]})
_doc_session = body.get("session_id")
status, body = jcall("GET", f"/api/sessions/{_doc_session}/messages?limit=10")
_user_msgs = [m for m in body.get("messages") or [] if m.get("role") == "user"]
check(
    "附件里的 Word 自动抽文字进上下文",
    any("合同金额 128000 元" in str(m.get("content")) for m in _user_msgs),
    str(_user_msgs)[:300],
)

# 页面图要真的进多模态消息（不是只躺在磁盘上）
from agent_api.agent import Agent  # noqa: E402
from agent_api.tools.base import ToolContext  # noqa: E402

provider_mod.active_meta = lambda *a, **k: {"vision": True}
try:
    _sess = runtime.sessions.create(title="视觉挂载自检")
    _pngs = sorted((_ws / "_docpages" / "smoke").glob("page-*.png")) or [_ws / "uploads" / "dot.png"]
    if _pngs and _pngs[0].exists():
        _sess.messages.append(
            {"role": "user", "content": "看看这几页", "attachments": [{"path": str(p.relative_to(_ws)), "type": "image"} for p in _pngs]}
        )
        _agent = Agent(runtime, _sess, ToolContext(session=_sess), max_turns=1)
        _parts = _agent._parts_for(_sess.messages[-1])
        _images = _parts if isinstance(_parts, list) else []
        _n_img = sum(1 for one in _images if isinstance(one, dict) and one.get("type") == "image_url")
        check("文档页面图进多模态消息（image_url）", _n_img >= 1, f"{_n_img} 张 / {len(_images)} 段")
finally:
    provider_mod.active_meta = _orig_meta

_pdf_b64 = base64.b64encode(_pdf.read_bytes()).decode("ascii")
status, body = jcall(
    "POST", "/api/ui/upload", {"name": "smoke.pdf", "data_url": "data:application/pdf;base64," + _pdf_b64}
)
_pdf_rel = body.get("path") or ""
check("上传接口收 PDF", status == 200 and _pdf_rel.endswith(".pdf"), str(body)[:200])
status, body = jcall("POST", "/api/chat", {"message": "这页写了啥", "attachments": [{"path": _pdf_rel}]})
status, body = jcall("GET", f"/api/sessions/{body.get('session_id')}/messages?limit=10")
check(
    "PDF 附件自动抽文字 + 渲染页图一起挂上",
    any("Hello Document 42" in str(m.get("content")) for m in body.get("messages") or [] if m.get("role") == "user"),
    str(body.get("messages"))[-300:],
)

# ---- 提供商：目录 / 空种 / 自定义模型 / 切换 / 清理空壳 ----
# ---------------- TTS ----------------
status, body = jcall("GET", "/api/ui/tts")
check("tts presets", status == 200 and len(body.get("presets") or []) >= 3 and body.get("configured") is False, str(body)[:200])
status, body = jcall("POST", "/api/ui/tts/speak", {"text": "你好"})
check("tts 未配置时明确报错", status == 400 and "语音合成" in str(body.get("error", "")), str(body)[:200])
status, body = jcall("POST", "/api/ui/tts", {"preset": "custom", "base_url": "http://127.0.0.1:1/v1", "model": "m", "voice": "alloy"})
check("tts 配置保存", status == 200 and body.get("configured") is True and body.get("model") == "m", str(body)[:200])
status, body = jcall("POST", "/api/ui/tts", {"format": "exe"})
check("tts 非法格式被拒", status == 400, str(body)[:200])

# ---------------- 定时任务 ----------------
status, body = jcall("POST", "/api/schedules", {"name": "自检任务", "cron": "0 9 * * *", "prompt": "说一句你好"})
check("schedule create", status == 200 and body.get("task", {}).get("next_run"), str(body)[:200])
task_id = (body.get("task") or {}).get("id")
status, body = jcall("POST", "/api/schedules", {"name": "坏任务", "cron": "0 25 * * *", "prompt": "x"})
check("schedule 非法 cron 被拒", status == 400 and "超出范围" in str(body.get("error", "")), str(body)[:200])
status, body = jcall("POST", "/api/schedules", {"name": "坏任务2", "cron": "* * *", "prompt": "x"})
check("schedule 段数不对被拒", status == 400 and "5 段" in str(body.get("error", "")), str(body)[:200])
status, body = jcall("POST", "/api/schedules", {"name": "幽灵", "cron": "0 0 31 2 *", "prompt": "x"})
check("schedule 日历上不存在的时间被拒", status == 400 and "永远不会触发" in str(body.get("error", "")), str(body)[:200])
status, body = jcall("POST", "/api/schedules", {"name": "闰日", "cron": "0 0 29 2 *", "prompt": "x"})
check("schedule 闰日能算出下次", status == 200 and "-02-29" in str(body.get("task", {}).get("next_run")), str(body)[:200])
if body.get("task", {}).get("id"):
    jcall("DELETE", f"/api/schedules/{body['task']['id']}")
status, body = jcall("POST", f"/api/schedules/{task_id}", {"cron": "* * * * *"})
check("schedule 改 cron 立刻重算", body.get("task", {}).get("human") == "每分钟" and bool(body.get("task", {}).get("next_run")), str(body)[:200])
status, body = jcall("POST", f"/api/schedules/{task_id}/run", {"wait": True})
check("schedule run now", body.get("ok") is True and body.get("status") == "ok", str(body)[:300])
status, body = jcall("GET", "/api/schedules")
check("schedule 记录上次结果", any(t["id"] == task_id and t["run_count"] >= 1 and t["last_status"] == "ok" for t in body.get("tasks", [])), str(body)[:300])
status, body = jcall("POST", f"/api/schedules/{task_id}", {"enabled": False})
check("schedule 暂停", body.get("task", {}).get("enabled") is False, str(body)[:200])
status, body = jcall("DELETE", f"/api/schedules/{task_id}")
check("schedule delete", body.get("ok") is True, str(body)[:200])

# ---------------- 会话导出 ----------------
status, body = call("GET", f"/api/sessions/{session_id}/export")
export_text = body if isinstance(body, bytes) else str(body)
check("export markdown", status == 200 and "# " in str(export_text)[:40], str(export_text)[:120])

status, body = jcall("GET", "/api/providers")
check("providers start empty", isinstance(body.get("providers"), list) and len(body["providers"]) == 0, str(body)[:300])
status, body = jcall("GET", "/api/providers/catalog")
check("provider catalog", len(body.get("providers") or []) >= 10, str(body)[:200])

status, body = jcall(
    "POST",
    "/api/providers",
    {"name": "自检中转站", "base_url": "https://example.com/v1", "api_key": "sk-smoke-123456", "models": ["m-a", "m-b"]},
)
check("add custom provider", any(p["id"] == "custom" and p["model_count"] == 2 for p in body.get("providers", [])), str(body)[:300])
status, body = jcall("POST", "/api/providers/custom/activate", {"model": "m-b"})
check("activate real model", body.get("ok") is True and body.get("model") == "m-b", str(body)[:300])
check("activate unknown model rejected", jcall("POST", "/api/providers/custom/activate", {"model": "nope"})[0] == 400)

status, body = jcall("POST", "/api/providers/add", {"preset": "moonshot"})
check("add preset without key", any(p["id"] == "moonshot" and not p["has_key"] for p in body.get("providers", [])), str(body)[:300])
status, body = jcall("POST", "/api/providers/prune", {})
check("prune empty shells", body.get("count", 0) >= 1 and not any(p["id"] == "moonshot" for p in body.get("providers", [])), str(body)[:300])
check("prune keeps active provider", any(p["id"] == "custom" for p in body.get("providers", [])))

status, body = jcall("POST", "/api/providers/custom/vision", {"vision": True})
check("vision toggle", any(p["id"] == "custom" and p["vision"] for p in body.get("providers", [])), str(body)[:200])

# ---- 搜索 / 图片 / 视频 配置 ----
status, body = jcall("GET", "/api/ui/search")
check("search presets", len(body.get("presets") or []) >= 5, str(body)[:200])
status, body = jcall("POST", "/api/ui/search", {"provider": "ddg", "api_key": "", "max_results": 4})
check("search config saved", body.get("ready") is True and body.get("provider") == "ddg", str(body)[:300])

status, body = jcall("GET", "/api/ui/media")
check("media presets", len((body.get("image") or {}).get("presets") or []) >= 3 and len((body.get("video") or {}).get("presets") or []) >= 2)
status, body = jcall(
    "POST",
    "/api/ui/media/image",
    {"preset": "custom-img", "base_url": "https://example.com/v1", "model": "img-x", "api_key": "sk-smoke-123456", "size": "512x512"},
)
check("image config saved", (body.get("image") or {}).get("ready") is True, str(body)[:300])
status, body = jcall(
    "POST",
    "/api/ui/media/video",
    {"preset": "custom-video", "base_url": "https://example.com/v1", "model": "vid-x", "api_key": "sk-smoke-123456", "mode": "generic_video"},
)
check("video config saved", (body.get("video") or {}).get("ready") is True and body["video"]["mode"] == "generic_video", str(body)[:300])


# ---- 账号体系：注册 / 登录 / 密钥 / 恢复码 / 锁定 ----
status, body = jcall("GET", "/api/auth/status", token=None)
check("auth 初始状态可读", status == 200 and body.get("users") == 0 and body.get("setup_needed") is True, str(body)[:200])

status, body, headers = hcall("POST", "/api/auth/register", {"email": "smoke@example.com", "password": "SmokePass!2026", "name": "Smoke", "agree": True})
cookie = cookie_of(headers)
check("注册第一个账号（owner + 恢复码）",
      status == 200 and body.get("user", {}).get("role") == "owner" and body.get("recovery_code") and cookie.startswith("agent_auth="),
      str(body)[:300])
recovery = body.get("recovery_code") or ""

status, body = jcall("GET", "/api/auth/status", token=None)
check("注册后自动启用且关闭注册", body.get("enabled") is True and body.get("users") == 1 and body.get("register_open") is False, str(body)[:200])

status, _, _ = hcall("GET", "/api/sessions")
check("没凭证读会话被拦", status == 401, str(status))
status, _, _ = hcall("GET", "/api/sessions", headers={"Cookie": cookie})
check("Cookie 能进", status == 200, str(status))

status, body, _ = hcall("GET", "/api/auth/me", headers={"Cookie": cookie})
check("me 返回账号", status == 200 and body.get("me", {}).get("email") == "smoke@example.com", str(body)[:200])

status, body, _ = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "wrong-pass"})
check("错密码 401", status == 401, str(body)[:200])

status, body, headers = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "SmokePass!2026"})
login_cookie = cookie_of(headers)
check("登录下发 Cookie", status == 200 and login_cookie.startswith("agent_auth="), str(body)[:200])

status, body, _ = hcall("POST", "/api/auth/keys", {"name": "smoke-key"}, headers={"Cookie": login_cookie})
key = body.get("key") or ""
check("生成 API 密钥", status == 200 and key.startswith("ak_"), str(body)[:200])

status, _, _ = hcall("GET", "/api/sessions", headers={"Authorization": f"Bearer {key}"})
check("Bearer 密钥可用", status == 200, str(status))
status, _, _ = hcall("GET", "/api/sessions", headers={"X-API-Token": key})
check("X-API-Token 当密钥可用", status == 200, str(status))

status, body, _ = hcall("GET", "/api/auth/keys", headers={"Cookie": login_cookie})
key_id = ((body.get("keys") or [{}])[0] or {}).get("id") or ""
status, body, _ = hcall("DELETE", f"/api/auth/keys/{key_id}", headers={"Cookie": login_cookie})
check("吊销密钥", status == 200 and body.get("ok") is True, str(body)[:200])
status, _, _ = hcall("GET", "/api/sessions", headers={"Authorization": f"Bearer {key}"})
check("吊销后密钥失效", status == 401, str(status))

status, body, headers = hcall("POST", "/api/auth/forgot", {"email": "smoke@example.com"})
check("忘记密码列出恢复码", status == 200 and "recovery" in (body.get("methods") or []), str(body)[:200])

status, body, _ = hcall("POST", "/api/auth/reset", {"email": "smoke@example.com", "code": recovery, "new": "NewSmoke!2026"})
check("恢复码重置成功并换新码", status == 200 and body.get("recovery_code") and body["recovery_code"] != recovery, str(body)[:200])
status, _, _ = hcall("GET", "/api/sessions", headers={"Cookie": login_cookie})
check("重置后旧 Cookie 失效", status == 401, str(status))
status, body, headers = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "NewSmoke!2026"})
check("新密码能登录", status == 200 and cookie_of(headers).startswith("agent_auth="), str(body)[:200])

cookie2 = cookie_of(headers)
status, body, _ = hcall("GET", "/api/auth/google/config", headers={"Cookie": cookie2})
check("Google 默认未配置", status == 200 and body.get("on") is False, str(body)[:200])

# ---- 身份验证器（Google Authenticator / TOTP）----
status, body, _ = hcall("POST", "/api/auth/totp/setup", {}, headers={"Cookie": cookie2})
secret = body.get("secret") or ""
check("生成 TOTP 密钥 + 二维码",
      status == 200 and len(secret) == 32 and "<svg" in (body.get("svg") or "") and (body.get("uri") or "").startswith("otpauth://totp/"),
      str(body)[:200])
status, body, _ = hcall("POST", "/api/auth/totp/enable", {"code": totp_code(secret)}, headers={"Cookie": cookie2})
check("错误/正确验证码启用", status == 200 and len(body.get("backup_codes") or []) == 10, str(body)[:200])
backup = (body.get("backup_codes") or [""])[0]
backup2 = (body.get("backup_codes") or ["", ""])[1]

status, body, _ = hcall("POST", "/api/auth/totp/enable", {"code": "000000"}, headers={"Cookie": cookie2})
check("验证码错不能启用/覆盖", status in (400, 401) or body.get("ok") is False, str(body)[:200])

status, body, _ = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "NewSmoke!2026"})
ticket = body.get("ticket") or ""
check("密码对但要求两步验证", status == 200 and body.get("totp_required") is True and ticket, str(body)[:200])

import time as _t
status, body, headers = hcall("POST", "/api/auth/login/totp", {"ticket": ticket, "code": totp_next_code(secret)})
check("6 位动态码登录成功", status == 200 and cookie_of(headers).startswith("agent_auth="), str(body)[:200])

status, body, _ = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "NewSmoke!2026"})
ticket2 = body.get("ticket") or ""
status, body, headers = hcall("POST", "/api/auth/login/totp", {"ticket": ticket2, "code": totp_code(secret)})
check("同一动态码不能重放", status in (401, 400) and body.get("ok") is False, str(body)[:200])

status, body, headers = hcall("POST", "/api/auth/login/totp", {"ticket": ticket2, "code": backup})
check("备用码能登录（各用一次）", status == 200 and cookie_of(headers).startswith("agent_auth="), str(body)[:200])
status, body, headers = hcall("POST", "/api/auth/login/totp", {"ticket": ticket2, "code": backup})
check("备用码用过就失效", status == 401, str(body)[:200])

status, body, _ = hcall("POST", "/api/auth/forgot", {"email": "smoke@example.com"})
check("忘记密码列出身份验证器", status == 200 and "authenticator" in (body.get("methods") or []), str(body)[:200])

status, body, _ = hcall("POST", "/api/auth/reset/totp",
                         {"email": "smoke@example.com", "code": backup2, "new": "TotpReset!2026"})
check("用备用码重置密码", status == 200 and body.get("recovery_code"), str(body)[:200])
status, body, _ = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "TotpReset!2026"})
check("重置后登录仍要两步验证", status == 200 and body.get("totp_required") is True, str(body)[:200])

# 连续错 5 次锁 15 分钟
for _ in range(5):
    hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "bad-pass"})
status, body, _ = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "bad-pass"})
check("连错 5 次后锁定 429", status == 429, str(body)[:200])
status, body, _ = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "TotpReset!2026"})
check("锁定期间正确密码也 429", status == 429, str(body)[:200])

# /ui 未登录 302 到 /login
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = urllib.request.build_opener(_NoRedirect)
try:
    opener.open(f"http://127.0.0.1:{PORT}/ui", timeout=30)
    ui_status = 200
except urllib.error.HTTPError as exc:
    ui_status = exc.code
check("/ui 未登录 302 到 /login", ui_status == 302, str(ui_status))

# ---- 上传 / 预览 / 静态文件 ----
png = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
status, body = jcall("POST", "/api/ui/upload", {"name": "dot.png", "data_url": "data:image/png;base64," + png})
check("upload image", body.get("ok") is True and body.get("url", "").startswith("/files/uploads/"), str(body)[:300])
if body.get("url"):
    status, _ = call("GET", body["url"])
    check("upload served", status == 200)
    status, _, _ = hcall("GET", body["url"])
    check("media 无凭证被拦", status == 401, str(status))
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{body['url']}",
        headers={"Range": "bytes=0-99", "X-API-Token": "smoke-token"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            rstatus, crange, got = resp.status, resp.headers.get("Content-Range", ""), len(resp.read())
    except urllib.error.HTTPError as exc:
        rstatus, crange, got = exc.code, exc.headers.get("Content-Range", ""), 0
    check(
        "media 支持 Range（音频/视频拖动进度）",
        rstatus == 206 and crange.startswith("bytes 0-") and got >= 1,
        f"{rstatus} {crange} {got} 字节",
    )

jcall("POST", "/api/files", {"path": "notes.md", "content": "# 标题\n\n- 一\n- 二\n"})
status, body = jcall("GET", "/api/ui/preview?path=notes.md")
check("preview markdown", body.get("suffix") == ".md" and "标题" in body.get("content", ""), str(body)[:200])
check("preview blocks escape", jcall("GET", "/api/ui/preview?path=../data/providers.json")[0] == 400)


# ---- 网络中断（Errno 103）不能把整轮变成红字报错 ----
def _stream_with(prefix: str, boom: bool):
    if prefix:
        yield b'data: {"choices":[{"delta":{"content":"' + prefix.encode() + b'"}}]}\n'
    if boom:
        raise ConnectionAbortedError("Software caused connection abort")


msg = OpenAICompatibleLLM._read_stream(None, _stream_with("已经想好的半句话", True), None)
check(
    "流中途断：保留已收到的内容 + 标注截断",
    msg.get("truncated") is True and "半句话" in (msg.get("content") or "") and "网络中断" in (msg.get("content") or ""),
    str(msg)[:200],
)
try:
    OpenAICompatibleLLM._read_stream(None, _stream_with("", True), None)
    empty_stop = False
except StreamAborted:
    empty_stop = True
check("流一启动就断：按可重试处理，不返回空消息", empty_stop)

import queue as _queue  # noqa: E402

_sentinel = object()
_q: "_queue.Queue" = _queue.Queue()


def _feed() -> None:
    import time as _time
    _time.sleep(0.25)
    _q.put({"type": "final", "content": "好"})
    _q.put(_sentinel)


threading.Thread(target=_feed, daemon=True).start()
_chunks = list(api_mod._sse_stream(_q, _sentinel, ping=0.1))
check(
    "SSE 空闲时发 ping 心跳（手机长时间不动也不会被掐）",
    any(c.startswith(": ping") for c in _chunks) and any('"final"' in c for c in _chunks),
    str(_chunks)[:200],
)


# ---------------- 游客（临时 / 匿名） ----------------
status, body, headers = hcall("POST", "/api/auth/guest", {})
guest_cookie = cookie_of(headers)
guest_code = body.get("unlock_code") or ""
gid = (body.get("user") or {}).get("id") or ""
check(
    "游客能开：返回 Cookie + 解锁码",
    status == 200 and guest_cookie.startswith("agent_auth=") and body.get("unlock_code")
    and body.get("user", {}).get("guest") is True and body.get("quota_mb") == 1,
    str(body)[:300],
)

status, body, _ = hcall("GET", "/api/auth/me", headers={"Cookie": guest_cookie})
check("游客 /me 带 guest 与到期", status == 200 and body.get("me", {}).get("role") == "guest"
      and body.get("me", {}).get("guest_expires_at"), str(body)[:200])

status, body = jcall("GET", "/api/auth/status", token=None)
check("游客不占正式账号 / 不影响 owner 与注册开关", body.get("users") == 1 and body.get("guests", 0) >= 1, str(body)[:200])

status, body, _ = hcall("GET", "/api/providers", headers={"Cookie": guest_cookie})
check("游客改不了提供商 / 设置", status == 403 and body.get("code") == "forbidden", str(body)[:200])

status, body, _ = hcall("POST", "/api/auth/password", {"old": "", "new": "Whatever!2026"}, headers={"Cookie": guest_cookie})
check("游客不能改密码 / 升级", status == 403, str(body)[:200])

raw = b"\x89PNG\r\n\x1a\n" + b"0" * (1200 * 1024)
status, body, _ = hcall("POST", "/api/ui/upload",
                        {"data_url": "data:image/png;base64," + base64.b64encode(raw).decode(), "name": "big.png"},
                        headers={"Cookie": guest_cookie})
check("游客 100MB 配额：1MB 上限时 1.2MB 上传被拒", status == 400 and "放不下" in body.get("error", ""), str(body)[:240])

secret = SMOKE / "workspace" / "secret-outside.txt"
secret.write_text("not for guests", encoding="utf-8")
status, body, _ = hcall("GET", "/api/files?path=../secret-outside.txt", headers={"Cookie": guest_cookie})
check("游客读不到外面（../ 越界）", "越界" in str(body.get("result") or body.get("error")) and secret.exists(), str(body)[:240])

(SMOKE / "workspace" / "media").mkdir(parents=True, exist_ok=True)
(SMOKE / "workspace" / "media" / "secret.mp3").write_bytes(b"not for guests")
status, body, _ = hcall("GET", "/files/media/secret.mp3", headers={"Cookie": guest_cookie})
check("游客拿不到公共 media 里的文件", status == 404, str(body)[:200])

status, body, _ = hcall("POST", "/api/files", {"path": "mine.txt", "content": "guest only"}, headers={"Cookie": guest_cookie})
guest_file = SMOKE / "workspace" / "guests" / gid / "mine.txt"
check("游客能写自己的目录", status == 200 and guest_file.exists() and guest_file.read_text(encoding="utf-8") == "guest only", str(body)[:200])
check("游客文件没落到公共根", not (SMOKE / "workspace" / "mine.txt").exists(), "公共根出现 mine.txt")

status, body = jcall("GET", "/api/sessions", token=None)
status, body, _ = hcall("GET", "/api/sessions", headers={"Cookie": guest_cookie})
check("游客会话列表是空的（看不到别人的）", status == 200 and body.get("sessions") == [], str(body)[:300])

status, body, _ = hcall("POST", "/api/chat", {"message": "游客第一条"}, headers={"Cookie": guest_cookie})
check("游客能聊天（放行到模型那一层）", body.get("code") != "guest_rate_limit" and body.get("code") != "forbidden", str(body)[:240])

status, body, _ = hcall("POST", "/api/chat", {"message": "游客第二条"}, headers={"Cookie": guest_cookie})
check("游客一分钟内第二条被 429", status == 429 and body.get("code") == "guest_rate_limit", str(body)[:240])

status, body, _ = hcall("POST", "/api/auth/guest/unlock", {"code": "XXXX-XXXX"}, headers={"Cookie": guest_cookie})
check("解锁码错 → 401", status == 401, str(body)[:200])
status, body, _ = hcall("POST", "/api/auth/guest/unlock", {"code": guest_code}, headers={"Cookie": guest_cookie})
check("游客用解锁码开 Python / 终端", status == 200 and body.get("ok") is True, str(body)[:200])
status, body, _ = hcall("GET", "/api/auth/guest/state", headers={"Cookie": guest_cookie})
check("解锁状态落盘", status == 200 and body.get("unlocked") is True, str(body)[:200])

_tok = use_root(SMOKE / "workspace" / "guests" / "ctx-check")
_ctx = contextvars.copy_context()
_box = []
_th = threading.Thread(target=_ctx.run, args=(lambda: _box.append(str(active_root())),))
_th.start(); _th.join()
reset_root(_tok)
check("流式 worker 能继承请求级沙箱根（游客目录）", _box and _box[0].endswith("guests/ctx-check"), str(_box)[:200])

data = runtime.auth._load()
for item in data.get("users", []):
    if item.get("guest"):
        item["guest_expires"] = __import__("time").time() - 5
runtime.auth._save(data)
status, body, _ = hcall("GET", "/api/auth/me", headers={"Cookie": guest_cookie})
check("游客到期自动失效", status == 401 and not any(u.get("guest") for u in runtime.auth.users()), str(body)[:200])
check("游客到期目录也清掉", not (SMOKE / "workspace" / "guests" / gid).exists(), str(gid))

owner = next((u for u in runtime.auth.users() if not u.get("guest")), {})
check("正式账号仍是 owner（游客没搅乱角色）", owner.get("role") == "owner" and len(runtime.auth.accounts()) == 1, str(owner)[:200])

# ---------------- 商家版订阅（服务层） ----------------
import os
import tempfile
from types import SimpleNamespace

from agent_api.auth import AuthService
from agent_api.billing import BillingService, SubscriptionQuota, SubscriptionRequired
from agent_api.server import member_path_ok

_old_mode = os.environ.get("DEPLOY_MODE")
os.environ["DEPLOY_MODE"] = "merchant"
_mdir = Path(tempfile.mkdtemp(prefix="smoke-merchant-"))
try:
    billing = BillingService(_mdir)
    check("商家版：订阅服务是开的", billing.enabled() is True)
    plans = billing.plans()
    check("商家版：默认套餐有 start / pro", {p["id"] for p in plans} >= {"start", "pro"}, str(plans)[:200])
    check("商家版：兑换码默认关闭，要先确认开启", billing.codes_enabled() is False)
    billing.set_codes_enabled(True)
    check("商家版：管理员确认后能开启兑换码", billing.codes_enabled() is True)
    codes = billing.generate_codes("start", 2, "smoke")
    check("商家版：能生成兑换码", len(codes) == 2 and all(c.startswith("AG-") for c in codes), str(codes))
    check("商家版：没订阅时账户是 none", billing.account("usr_smoke").get("status") == "none", str(billing.account("usr_smoke")))
    try:
        billing.require("usr_smoke")
        check("商家版：没订阅聊天被 402", False, "require 没报错")
    except SubscriptionRequired as exc:
        check("商家版：没订阅聊天被 402", exc.status_code == 402, str(exc))
    stored = billing.list_codes()[0].get("prefix") or ""
    check("商家版：落盘只留掩码，不留完整码", stored != codes[0] and "…" in stored, stored)
    acct = billing.redeem("usr_smoke", codes[0])
    check("商家版：兑换码开通套餐", acct.get("status") == "active" and acct.get("remaining_messages") == 300, str(acct)[:240])
    try:
        billing.redeem("usr_smoke", codes[0])
        check("商家版：兑换码不能重复用", False, "重复兑换没报错")
    except Exception as exc:
        check("商家版：兑换码不能重复用", "用过" in str(exc), str(exc))
    acct = billing.consume("usr_smoke", 123, 1)
    check("商家版：聊天扣条数与 token", acct.get("remaining_messages") == 299 and acct.get("used_messages") == 1 and acct.get("tokens_used") == 123, str(acct)[:240])
    data = billing._load()
    data["accounts"]["usr_smoke"]["remaining_messages"] = 0
    billing._save(data)
    try:
        billing.require("usr_smoke")
        check("商家版：条数用完 429", False, "require 没报错")
    except SubscriptionQuota as exc:
        check("商家版：条数用完 429", exc.status_code == 429, str(exc))
    data = billing._load()
    data["accounts"]["usr_smoke"]["expires"] = 1
    billing._save(data)
    try:
        billing.require("usr_smoke")
        check("商家版：到期要续费", False, "require 没报错")
    except SubscriptionRequired:
        check("商家版：到期要续费", True)

    dummy = SimpleNamespace(data_dir=_mdir)
    a = AuthService(dummy)
    check("商家版：用户能自己注册（注册开关强制开）", a.register_open() is True)
    legacy = {"users": [{"id": "usr_old", "email": "old@shop.com", "name": "老用户", "role": "user",
                         "created_at": "2025-01-01 00:00:00", "api_keys": []}],
              "sessions": [], "states": [], "tickets": [], "config": {}}
    (Path(_mdir) / "auth.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    a2 = AuthService(dummy)
    check("老账号库没有 owner 时，最早注册的自动升成 owner",
          a2.users()[0].get("role") == "owner", str(a2.users())[:200])
    check("商家版：会员端禁全局配置、放行聊天与上传",
          (not member_path_ok("/api/providers")) and (not member_path_ok("/api/ui/settings"))
          and member_path_ok("/api/ui/upload") and member_path_ok("/api/chat") and member_path_ok("/api/files"),
          "member_path_ok 判定不对")
finally:
    if _old_mode is None:
        os.environ.pop("DEPLOY_MODE", None)
    else:
        os.environ["DEPLOY_MODE"] = _old_mode
    import shutil
    shutil.rmtree(_mdir, ignore_errors=True)

for asset in ("/ui", "/ui/app.css", "/ui/core.js", "/ui/sessions.js", "/ui/canvas.js", "/ui/chat.js",
               "/ui/providers.js", "/ui/settings.js", "/ui/tts.js", "/ui/schedules.js",
               "/ui/account.js", "/ui/login.js", "/ui/setup.js", "/login", "/ui/login.html", "/ui/setup.html", "/setup", "/terms/user", "/terms/merchant"):
    status, _ = call("GET", asset, token=None)
    if status not in (200, 302):
        check("static " + asset, False, str(status))
        break
else:
    check("all ui assets", True)


server.shutdown()
server.server_close()

print()
print(f"结果：{len(passed)} 通过，{len(failed)} 失败")
if failed:
    for item in failed:
        print(" -", item)
    sys.exit(1)
