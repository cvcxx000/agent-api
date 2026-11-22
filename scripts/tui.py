#!/usr/bin/env python3
"""终端版界面：零依赖，直接和本机的 agent-api 流式对话。

    python3 scripts/tui.py                       # 默认 http://127.0.0.1:8000
    python3 scripts/tui.py --base http://127.0.0.1:8000 --token 你的TOKEN

命令（以 / 开头）：
    /help            看全部命令
    /new             新会话
    /model           选提供商和模型（列表里挑编号，立即生效）
    /providers       列出提供商与模型
    /search 关键词    直接查搜索服务
    /image 提示词     直接生成图片
    /video 提示词     直接生成视频（异步，会等）
    /md 路径          把 workspace 里的 Markdown 渲染到终端
    /file 路径        看纯文本
    /quit            退出
其余输入都会作为一句话发给 Agent，思考/正文/工具调用都是流式打印。
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import urllib.error
import urllib.request

TTY = sys.stdout.isatty()


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


DIM = "2"
CYAN = "36"
GREEN = "32"
RED = "31"
MAGENTA = "35"
BOLD = "1"


class Api:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token

    def headers(self) -> dict[str, str]:
        head = {"Content-Type": "application/json"}
        if self.token:
            head["X-API-Token"] = self.token
        return head

    def get(self, path: str) -> dict:
        req = urllib.request.Request(self.base + path, headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise SystemExit("接口错误：" + exc.read().decode("utf-8", "replace")[:400]) from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"连不上 {self.base}：{exc.reason}") from exc

    def post(self, path: str, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"连不上 {self.base}：{exc.reason}") from exc


def wrap(text: str, indent: str = "  ") -> str:
    width = 100
    return "\n".join(textwrap.wrap(text, width=width) or [""]) if len(text) < 2000 else text


def render_md(text: str) -> str:
    """终端里的 Markdown：标题加粗、代码块留框、链接留文字、图片/视频留地址。"""
    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip()
            out.append(c(DIM, "┌─ " + (lang or "code") + " " + "─" * 24))
            in_code = not in_code
            if not in_code:
                out.append(c(DIM, "└" + "─" * 30))
            continue
        if in_code:
            out.append(c(DIM, "│ " + line))
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append(c(BOLD, line[level:].strip()))
            continue
        if line.startswith("- ") or line.startswith("* "):
            out.append("• " + line[2:])
            continue
        if line.startswith("MEDIA_IMAGE:"):
            out.append(c(CYAN, "图片：" + line.split(":", 1)[1].strip()))
            continue
        if line.startswith("MEDIA_VIDEO:"):
            out.append(c(CYAN, "视频：" + line.split(":", 1)[1].strip()))
            continue
        if line.startswith("!["):
            continue  # 上一行已经给出地址
        out.append(line.replace("**", ""))
    return "\n".join(out)


def stream_chat(api: Api, message: str, session: list[str], state: dict) -> None:
    body: dict = {"message": message}
    if state.get("session"):
        body["session_id"] = state["session"]
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api.base + "/api/chat/stream", data=payload, method="POST", headers=api.headers()
    )
    think_open = False
    content_open = False
    tools: dict = {}

    print(c(DIM, "你 › ") + message)
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                kind = ev.get("type")
                if kind == "reasoning_delta":
                    if not think_open:
                        print(c(DIM, "思考 › "), end="")
                        think_open = True
                    print(c(DIM, ev.get("text", "")), end="", flush=True)
                elif kind == "content_delta":
                    if think_open:
                        print()
                        think_open = False
                    if not content_open:
                        print(c(GREEN, "Agent › "), end="")
                        content_open = True
                    print(ev.get("text", ""), end="", flush=True)
                elif kind == "assistant":
                    if think_open:
                        print()
                        think_open = False
                    if content_open:
                        print()
                        content_open = False
                    elif ev.get("content"):
                        print(c(GREEN, "Agent › ") + render_md(ev["content"]))
                elif kind == "tool_call":
                    if think_open:
                        print()
                        think_open = False
                    if content_open:
                        print()
                        content_open = False
                    name = ev.get("name", "")
                    args = json.dumps(ev.get("arguments") or {}, ensure_ascii=False)
                    print(c(MAGENTA, f"  ⚙ {name} ") + c(DIM, args[:300]))
                    tools[ev.get("id") or name] = ev.get("turn")
                elif kind == "tool_result":
                    output = str(ev.get("output") or "")
                    lines = output.splitlines()
                    shown = lines[:8]
                    for one in shown:
                        print(c(DIM, "    │ " + one))
                    if len(lines) > 8:
                        print(c(DIM, f"    │ …（还有 {len(lines) - 8} 行）"))
                elif kind == "warning":
                    print(c(MAGENTA, "  ! " + str(ev.get("message")))
                elif kind == "error":
                    print(c(RED, "  错误：" + str(ev.get("message")))
                elif kind == "end":
                    if content_open:
                        print()
                        content_open = False
                elif kind == "final":
                    if content_open:
                        print()
                        content_open = False
                    if ev.get("session_id"):
                        state["session"] = ev["session_id"]
                    print(c(DIM, f"完成 · {ev.get('turns') or 0} 轮 · session {state.get('session') or '-'}"))
    except KeyboardInterrupt:
        print(c(RED, "\n（已中断显示；服务端可能还在跑，等它跑完再发下一句）"))
        think_open = False
        content_open = False
    except urllib.error.HTTPError as exc:
        print(c(RED, "HTTP " + str(exc.code) + "：" + exc.read().decode("utf-8", "replace")[:300]))
    except urllib.error.URLError as exc:
        print(c(RED, f"连不上：{exc.reason}"))
    finally:
        if think_open or content_open:
            print()


def cmd_model(api: Api) -> None:
    data = api.get("/api/providers")
    items = [p for p in data.get("providers", []) if p.get("models")]
    if not items:
        print(c(RED, "还没有模型：先跑 /providers，在网页「提供商 → 从目录添加 → 拉取模型」里配。"))
        return
    idx = 1
    flat: list[tuple[str, str]] = []
    for provider in items:
        print(c(BOLD, provider["name"]) + c(DIM, "  " + provider["base_url"]))
        for model in provider["models"]:
            mark = "*" if provider.get("is_active") and provider.get("active_model") == model else " "
            print(f" {c(DIM, f'[{idx}]')}{mark} {model}")
            flat.append((provider["id"], model))
            idx += 1
    raw = input(c(CYAN, "编号（回车取消）：")).strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(flat)):
        return
    pid, model = flat[int(raw) - 1]
    result = api.post(f"/api/providers/{pid}/activate", {"model": model})
    note = "（没 Key，进 Mock）" if result.get("mock_llm") else ""
    print(c(GREEN, f"已切到 {result.get('model')} @ {result.get('base_url')}{note}"))


def cmd_providers(api: Api) -> None:
    data = api.get("/api/providers")
    print(c(DIM, f"当前：{data.get('current', {}).get('model') or '未配置'}  {data.get('current', {}).get('base_url') or ''}"))
    for p in data.get("providers", []):
        bits = []
        bits.append("启用" if p.get("enabled") else "停用")
        bits.append(f"{p.get('model_count', 0)} 个模型")
        if p.get("is_active"):
            bits.append("当前")
        if not p.get("has_key"):
            bits.append("无 Key")
        print(f"· {c(BOLD, p['name'])} {c(DIM, p['base_url'])}  " + c(CYAN, " / ".join(bits)))
        for model in p.get("models", []):
            mark = "*" if p.get("is_active") and p.get("active_model") == model else " "
            print(c(DIM, f"    {mark} {model}"))


def cmd_search(api: Api, query: str) -> None:
    if not query.strip():
        print("用法：/search 关键词")
        return
    try:
        result = api.post("/api/ui/search/test", {"query": query, "count": 5})
    except RuntimeError as exc:
        print(c(RED, f"搜索失败：{exc}"))
        return
    print(c(DIM, f"服务：{result.get('provider')}"))
    for i, item in enumerate(result.get("results") or [], 1):
        print(f"{i}. {c(BOLD, item.get('title') or '')}")
        print(c(CYAN, "   " + (item.get("url") or "")))
        if item.get("snippet"):
            print(c(DIM, "   " + wrap(item["snippet"])[:400].replace("\n", "\n   ")))


def cmd_image(api: Api, prompt: str) -> None:
    if not prompt.strip():
        print("用法：/image 提示词")
        return
    try:
        result = api.post("/api/ui/media/image/generate", {"prompt": prompt})
    except RuntimeError as exc:
        print(c(RED, f"生成失败：{exc}"))
        return
    res = result.get("result") or {}
    print(c(GREEN, f"图片：{res.get('path')}（{int((res.get('bytes') or 0) / 1024)} KB）"))
    print(c(CYAN, api.base + str(res.get("url") or "")))


def cmd_video(api: Api, prompt: str) -> None:
    if not prompt.strip():
        print("用法：/video 提示词")
        return
    print(c(DIM, "提交中，出片要等几十秒到几分钟…"))
    try:
        result = api.post("/api/ui/media/video/generate", {"prompt": prompt})
    except RuntimeError as exc:
        print(c(RED, f"生成失败：{exc}"))
        return
    res = result.get("result") or {}
    print(c(GREEN, f"视频：{res.get('path')}（{int((res.get('bytes') or 0) / 1024)} KB）"))
    print(c(CYAN, api.base + str(res.get("url") or "")))


def cmd_md(api: Api, path: str) -> None:
    if not path.strip():
        print("用法：/md outputs/report.md")
        return
    try:
        result = api.get("/api/ui/preview?path=" + urllib.request.quote(path.strip()))
    except SystemExit as exc:
        print(c(RED, str(exc)))
        return
    print(render_md(result.get("content") or ""))


def cmd_file(api: Api, path: str) -> None:
    if not path.strip():
        print("用法：/file outputs/a.txt")
        return
    try:
        result = api.get("/api/ui/preview?path=" + urllib.request.quote(path.strip()))
    except SystemExit as exc:
        print(c(RED, str(exc)))
        return
    print(result.get("content") or "")


HELP = """\
命令：
  /help            看这份说明
  /new             新会话
  /model           选提供商和模型（列表挑编号）
  /providers       列出提供商与模型
  /search 关键词    查搜索服务
  /image 提示词     生成图片
  /video 提示词     生成视频
  /md 路径          渲染 workspace 里的 Markdown
  /file 路径        看纯文本
  /session         看当前 session id
  /quit            退出
其他输入直接发给 Agent。Ctrl+C 中断当前显示（不中断服务端任务）。"""


def main() -> None:
    parser = argparse.ArgumentParser(description="agent-api 终端界面")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="服务地址")
    parser.add_argument("--token", default="", help="X-API-Token（服务端没设就留空）")
    parser.add_argument("--session", default="", help="接着某个 session 聊")
    args = parser.parse_args()
    api = Api(args.base, args.token)
    state = {"session": args.session}

    print(c(BOLD, "agent-api") + c(DIM, f"  {api.base}   /help 看命令"))
    try:
        health = api.get("/health")
        print(c(DIM, "在线 · " + ("Mock 模式（未配 Key）" if health.get("mock_llm") else "已连真实模型")))
    except SystemExit as exc:
        print(c(RED, str(exc)))
        return

    while True:
        try:
            line = input(c(CYAN, "› ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in {"/quit", "/exit", "exit"}:
            break
        if line == "/help":
            print(HELP)
            continue
        if line == "/new":
            state["session"] = ""
            print(c(DIM, "已开新会话。"))
            continue
        if line == "/session":
            print(c(DIM, "session: " + (state.get("session") or "（下一句新建）")))
            continue
        if line == "/model":
            try:
                cmd_model(api)
            except (RuntimeError, SystemExit) as exc:
                print(c(RED, str(exc)))
            continue
        if line == "/providers":
            try:
                cmd_providers(api)
            except (RuntimeError, SystemExit) as exc:
                print(c(RED, str(exc)))
            continue
        if line.startswith("/search"):
            cmd_search(api, line[len("/search"):].strip())
            continue
        if line.startswith("/image"):
            cmd_image(api, line[len("/image"):].strip())
            continue
        if line.startswith("/video"):
            cmd_video(api, line[len("/video"):].strip())
            continue
        if line.startswith("/md"):
            cmd_md(api, line[len("/md"):].strip())
            continue
        if line.startswith("/file"):
            cmd_file(api, line[len("/file"):].strip())
            continue
        stream_chat(api, line, [], state)


if __name__ == "__main__":
    main()
