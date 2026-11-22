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

# ---- 上传 / 预览 / 静态文件 ----
png = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
status, body = jcall("POST", "/api/ui/upload", {"name": "dot.png", "data_url": "data:image/png;base64," + png})
check("upload image", body.get("ok") is True and body.get("url", "").startswith("/files/uploads/"), str(body)[:300])
if body.get("url"):
    status, _ = call("GET", body["url"], token=None)
    check("upload served", status == 200)

jcall("POST", "/api/files", {"path": "notes.md", "content": "# 标题\n\n- 一\n- 二\n"})
status, body = jcall("GET", "/api/ui/preview?path=notes.md")
check("preview markdown", body.get("suffix") == ".md" and "标题" in body.get("content", ""), str(body)[:200])
check("preview blocks escape", jcall("GET", "/api/ui/preview?path=../data/providers.json")[0] == 400)

for asset in ("/ui", "/ui/app.css", "/ui/core.js", "/ui/sessions.js", "/ui/canvas.js", "/ui/chat.js", "/ui/providers.js", "/ui/settings.js"):
    status, _ = call("GET", asset, token=None)
    if status != 200:
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
