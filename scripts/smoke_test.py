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

from agent_api.runtime import create_runtime  # noqa: E402
from agent_api.server import build_server  # noqa: E402

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

status, body = jcall("POST", "/api/chat", {"message": "自检：跑一下环境", "title": "smoke"})
step_names = [s.get("name") for s in body.get("steps", []) if s.get("type") == "tool"]
check("chat mock tool loop", status == 200 and "run_python" in step_names, json.dumps(body, ensure_ascii=False)[:400])

session_id = body.get("session_id")
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

status, body, headers = hcall("POST", "/api/auth/register", {"email": "smoke@example.com", "password": "SmokePass!2026", "name": "Smoke"})
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

status, body, _ = hcall("GET", "/api/auth/google/config", headers={"Cookie": cookie_of(headers)})
check("Google 默认未配置", status == 200 and body.get("on") is False, str(body)[:200])

# 连续错 5 次锁 15 分钟
for _ in range(5):
    hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "bad-pass"})
status, body, _ = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "bad-pass"})
check("连错 5 次后锁定 429", status == 429, str(body)[:200])
status, body, _ = hcall("POST", "/api/auth/login", {"email": "smoke@example.com", "password": "NewSmoke!2026"})
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

for asset in ("/ui", "/ui/app.css", "/ui/core.js", "/ui/sessions.js", "/ui/canvas.js", "/ui/chat.js",
               "/ui/providers.js", "/ui/settings.js", "/ui/tts.js", "/ui/schedules.js",
               "/ui/account.js", "/ui/login.js", "/login", "/ui/login.html"):
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
