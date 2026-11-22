"""终端交互式 REPL：多轮对话、实时看工具调用，纯 urllib 直连本地服务。

用法：
    python3 scripts/repl.py                       # 默认连 http://127.0.0.1:8000
    python3 scripts/repl.py --base http://1.2.3.4:8000 --token abc

内置命令：/new 开新会话  /session 看当前会话  /system <文本> 设系统提示词
           /system- 清空  /help 帮助  /exit 退出
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def parse_sse(response):
    """逐行读取 chunked SSE，尽量即时打印。"""
    buffer = ""
    for raw in response:
        buffer += raw.decode("utf-8", "replace")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            data = []
            for line in block.split("\n"):
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
            if data:
                try:
                    yield json.loads("\n".join(data))
                except json.JSONDecodeError:
                    continue


def short(text, limit=120):
    text = "" if text is None else str(text)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"


def describe(event):
    kind = event.get("type")
    if kind == "assistant":
        content = event.get("content") or ""
        if content.strip():
            print(f"\n\033[36m{content}\033[0m", end="", flush=True)
    elif kind == "tool_call":
        print(f"\n  \033[33m→ {event.get('name')}\033[0m {short(event.get('arguments'), 160)}", flush=True)
    elif kind == "tool_result":
        mark = "✗" if event.get("ok") is False else "✓"
        print(f"  \033[32m{mark} {short(event.get('output'), 300)}\033[0m", flush=True)
    elif kind == "warning":
        print(f"\n  ! {event.get('message')}", flush=True)
    elif kind == "error":
        print(f"\n  \033[31m错误：{event.get('message')}\033[0m", flush=True)
    elif kind == "final":
        return event
    return None


def run_turn(base, token, session_id, message, system_prompt):
    body = {"message": message, "stream": True}
    if session_id:
        body["session_id"] = session_id
    if system_prompt:
        body["system_prompt"] = system_prompt
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(
        base.rstrip("/") + "/api/agent/run",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=None) as response:
            final = None
            for event in parse_sse(response):
                got = describe(event)
                if got:
                    final = got
            print()
            return final
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        print(f"\033[31m请求失败 HTTP {exc.code}：{detail}\033[0m")
    except urllib.error.URLError as exc:
        print(f"\033[31m连不上服务（{base}）：{exc.reason}\033[0m")
    return None


HELP = """
命令：
  /new              开一个新会话
  /session          查看当前 session id
  /system <文本>    设置本轮起用的系统提示词
  /system-          取消自定义系统提示词
  /help             显示帮助
  /exit 或 /quit    退出
直接输入内容即为发消息，Ctrl+C 退出。
"""


def main():
    parser = argparse.ArgumentParser(description="Agent API 交互式终端")
    parser.add_argument("--base", default=os.getenv("AGENT_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.getenv("AGENT_TOKEN", ""))
    parser.add_argument("--system", default=os.getenv("AGENT_SYSTEM_PROMPT", ""))
    args = parser.parse_args()

    base, token = args.base, args.token
    session_id = ""
    system_prompt = args.system

    print(f"连接 {base} （/help 看命令，/exit 退出）")
    while True:
        try:
            line = input("\033[1m你>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            break
        if line == "/help":
            print(HELP)
            continue
        if line == "/new":
            session_id = ""
            print("已开新会话，下一句会建一个新 session。")
            continue
        if line == "/session":
            print(f"当前 session：{session_id or '(未创建)'}")
            continue
        if line.startswith("/system-"):
            system_prompt = ""
            print("已取消自定义系统提示词。")
            continue
        if line.startswith("/system "):
            system_prompt = line[len("/system "):].strip()
            print("系统提示词已更新（本轮起生效）。")
            continue
        if line.startswith("/"):
            print("未知命令，/help 查看。")
            continue

        final = run_turn(base, token, session_id, line, system_prompt)
        if final and final.get("session_id"):
            session_id = final["session_id"]

    print("再见。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
