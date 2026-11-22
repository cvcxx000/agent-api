#!/usr/bin/env python3
"""命令行客户端：直接和本机的 Agent API 对话。

用法：
    python3 scripts/chat_cli.py "帮我在 workspace 里建个 hello.py 并跑通"
    python3 scripts/chat_cli.py --stream "统计 workspace 下所有 .py 的行数"
    python3 scripts/chat_cli.py --session sess_xxx "继续刚才的任务"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def env_value(key: str, default: str = "") -> str:
    if os.getenv(key):
        return os.environ[key]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    return default


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent API 命令行客户端")
    parser.add_argument("message", nargs="+", help="要交给 Agent 的任务")
    parser.add_argument("--base-url", default=env_value("API_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=env_value("API_TOKEN", ""))
    parser.add_argument("--session", default=None, help="复用已有会话 id")
    parser.add_argument("--system", default=None, help="本轮替换系统提示词")
    parser.add_argument("--stream", action="store_true", help="用 SSE 流式输出过程")
    parser.add_argument("--show-steps", action="store_true", help="打印每次工具调用")
    args = parser.parse_args()

    payload = {"message": " ".join(args.message)}
    if args.session:
        payload["session_id"] = args.session
    if args.system:
        payload["system_prompt"] = args.system

    url = args.base_url.rstrip("/") + ("/api/chat/stream" if args.stream else "/api/chat")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["X-API-Token"] = args.token

    request = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=600) as resp:
            if args.stream:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    kind = event.get("type")
                    if kind == "tool_call":
                        print(f"[工具] {event.get('name')} {json.dumps(event.get('arguments'), ensure_ascii=False)[:200]}")
                    elif kind == "tool_result":
                        print(f"[结果] {event.get('name')}: {str(event.get('output'))[:300]}")
                    elif kind == "assistant" and event.get("content"):
                        print(f"\n[模型] {event['content']}")
                    elif kind == "error":
                        print(f"[错误] {event.get('message')}")
                    elif kind == "final":
                        print(f"\n=== 最终答复 ===\n{event.get('content')}\n(session: {event.get('session_id')})")
            else:
                body = json.loads(resp.read().decode("utf-8"))
                if args.show_steps:
                    for step in body.get("steps", []):
                        if step.get("type") == "tool":
                            print(f"[工具] {step.get('name')} -> {str(step.get('output'))[:300]}")
                print(body.get("content", ""))
                print(f"\n(session: {body.get('session_id')}, turns: {body.get('turns')})")
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"连不上 {args.base_url}：{exc.reason}\n先把服务跑起来：python3 run.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
