#!/usr/bin/env python3
"""交互式终端：和 Agent 多轮对话，边干活边看工具调用。

用法：
    python3 scripts/repl.py
    python3 scripts/repl.py --base-url http://127.0.0.1:8000 --token xxx

命令：
    /new          新建会话（清空上下文）
    /system X     替换系统提示词（下一轮生效）
    /session      显示当前 session id
    /help         帮助
    /exit 或 Ctrl+C 退出
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
BANNER = "输入任务直接回车发送；/help 看命令，/exit 退出。"


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


class Client:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.token = token
        self.session_id: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-API-Token"] = self.token
        return headers

    def _post(self, path: str, body: dict, timeout: float = 900) -> urllib.response.addinfourl:  # type: ignore[name-defined]
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method="POST", headers=self._headers())
        return urllib.request.urlopen(request, timeout=timeout)

    def health(self) -> dict:
        with urllib.request.urlopen(self.base + "/health", timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def set_system_prompt(self, prompt: str) -> None:
        if not self.session_id:
            print("（还没有会话，这条提示词会在新建会话时生效）")
            return
        self._post(f"/api/sessions/{self.session_id}/system-prompt", {"prompt": prompt, "mode": "replace"}).close()
        print("已替换系统提示词。")

    def chat(self, message: str) -> None:
        body: dict = {"message": message}
        if self.session_id:
            body["session_id"] = self.session_id
        printed_tool = False
        final = ""
        try:
            resp = self._post("/api/chat/stream", body)
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}", file=sys.stderr)
            return
        except urllib.error.URLError as exc:
            print(f"连不上 {self.base}：{exc.reason}", file=sys.stderr)
            return

        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind == "tool_call":
                    if not printed_tool:
                        print("\n  · 调用工具", flush=True)
                        printed_tool = True
                    print(f"\n    {event.get('name')} {json.dumps(event.get('arguments'), ensure_ascii=False)[:160]}", flush=True)
                elif kind == "tool_result":
                    text = str(event.get("output") or "").replace("\n", "\n    ")
                    print(f"    ↳ {text[:400]}", flush=True)
                elif kind == "assistant" and event.get("content"):
                    print(f"\n{event['content']}", end="", flush=True)
                elif kind == "warning":
                    print(f"\n[提示] {event.get('message')}")
                elif kind == "error":
                    print(f"\n[错误] {event.get('message')}")
                elif kind == "final":
                    if event.get("session_id"):
                        self.session_id = event["session_id"]
                    final = str(event.get("content") or "")
        print(f"\n\n(session: {self.session_id}, turns: 见上)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 交互式终端")
    parser.add_argument("--base-url", default=env_value("API_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=env_value("API_TOKEN", ""))
    args = parser.parse_args()

    client = Client(args.base_url, args.token)
    try:
        info = client.health()
        print(f"已连接 {args.base_url} · {info.get('service')} · mock={info.get('mock_llm')}")
    except Exception as exc:  # noqa: BLE001
        print(f"连不上 {args.base_url}：{exc}\n先把服务跑起来：python3 run.py", file=sys.stderr)
        return 1

    print(BANNER)
    pending_system: str | None = None
    while True:
        try:
            text = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            print("再见。")
            return 0
        if text == "/help":
            print(BANNER + "\n/new  /system <提示词>  /session  /exit")
            continue
        if text == "/new":
            client.session_id = None
            print("已新建会话。")
            if pending_system:
                print("（之前待应用的系统提示词请用 /system 重新设置）")
            continue
        if text == "/session":
            print(client.session_id or "（还没开始对话）")
            continue
        if text.startswith("/system"):
            prompt = text[len("/system"):].strip()
            if not prompt:
                print("用法：/system 你现在是一个只写中文简短结论的助手")
                continue
            client.set_system_prompt(prompt)
            pending_system = prompt
            continue
        client.chat(text)


if __name__ == "__main__":
    raise SystemExit(main())
