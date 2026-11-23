#!/usr/bin/env python3
"""终端版界面：零依赖，直接和本机的 agent-api 流式对话。

    python3 scripts/tui.py                       # 默认 http://127.0.0.1:8000
    python3 scripts/tui.py --base http://127.0.0.1:8000 --token 你的TOKEN

命令（以 / 开头）：
    /help            看全部命令
    /new             新会话
    /model           选提供商和模型（列表里挑编号，立即生效）
    /providers       列出提供商与模型
    /whoami          看当前身份（账号 / 密钥）
  /skills          列出所有技能（SKILL.md）
    /skill 名字       把技能载入当前会话
    /search 关键词    直接查搜索服务
    /image 提示词     直接生成图片
    /video 提示词     直接生成视频（异步，会等）
    /md 路径          把 workspace 里的 Markdown 渲染到终端
    /file 路径        看纯文本
  /open 路径        挂 PDF / Word（自动抽文字 + 前几页转图）
    /see 图片路径     把本地图片发给模型看图（下一句自动带上）
    /say 文字         合成语音（TTS），给出可播放的音频地址
    /cron            看定时任务，输编号立刻跑一次；/cron off|on 编号 开关
    /sessions        列出会话，输编号切换
    /quit            退出
其余输入都会作为一句话发给 Agent，思考/正文/工具调用都是流式打印。

打 / 回车看命令清单，Tab 补全命令名，`/skill ` 后 Tab 补全技能名。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TTY = sys.stdout.isatty()

# 拼 URL 时保留结构字符，其余（中文、空格、# 等）一律百分号编码。
_SAFE_CHARS = "/?&=%:+~@!$'()*,;[]._-"


def enc_path(path: str) -> str:
    """把带中文的路径编成合法 URL：/api/sessions/x/skills/数据报告 → ...%E6%95%B0...

    urllib 只认真 ASCII，直接拼中文会抛 UnicodeEncodeError（而且报的是一整段
    traceback，看起来像服务炸了）。所有请求路径统一从这里过一遍。
    """
    return urllib.parse.quote(str(path), safe=_SAFE_CHARS)


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

    def _auth_hint(self, code: int) -> str:
        if code != 401:
            return ""
        return ("这是没登录/没凭证。\n"
                "  · 网页：浏览器打开 /login 登录一次；\n"
                "  · 终端：在「设置 → 账号与安全 → API 密钥」生成 ak_…，然后 "
                "python3 scripts/tui.py --token ak_xxx\n"
                "  · 或者在服务端 .env 里设 API_TOKEN，用 --token 你的API_TOKEN")

    def get(self, path: str) -> dict:
        req = urllib.request.Request(self.base + enc_path(path), headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise SystemExit("接口错误：" + detail + ("\n" + self._auth_hint(exc.code) if exc.code == 401 else "")) from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"连不上 {self.base}：{exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001  URL 里出现编码不了的字符等
            raise SystemExit(f"请求失败（{path}）：{exc}") from exc

    def delete(self, path: str) -> dict:
        req = urllib.request.Request(self.base + enc_path(path), method="DELETE", headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {exc.code}：{detail}" + ("\n" + self._auth_hint(exc.code) if exc.code == 401 else "")) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"连不上 {self.base}：{exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"请求失败（{path}）：{exc}") from exc

    def post(self, path: str, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + enc_path(path), data=data, method="POST", headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {exc.code}：{detail}" + ("\n" + self._auth_hint(exc.code) if exc.code == 401 else "")) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"连不上 {self.base}：{exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001  URL 里出现编码不了的字符等
            raise RuntimeError(f"请求失败（{path}）：{exc}") from exc


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
        if line.startswith("MEDIA_AUDIO:"):
            url = line.split(":", 1)[1].strip()
            out.append(c(CYAN, "语音：" + url))
            out.append(c(DIM, "  手机点这个地址就能播"))
            continue
        if line.startswith("!["):
            continue  # 上一行已经给出地址
        out.append(line.replace("**", ""))
    return "\n".join(out)


def stream_chat(api: Api, message: str, session: list[str], state: dict) -> None:
    body: dict = {"message": message}
    attached = state.pop("attached", None) or []
    if attached:
        body["attachments"] = attached
    if state.get("session"):
        body["session_id"] = state["session"]
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api.base + "/api/chat/stream", data=payload, method="POST", headers=api.headers()
    )
    think_open = False
    content_open = False
    tools: dict = {}

    mark = ""
    if attached:
        mark = c(DIM, " [图 " + ", ".join(str(a.get("name") or a.get("path")) for a in attached) + "]")
    print(c(DIM, "你 › ") + message + mark)
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
                    warn = str(ev.get("message"))
                    print(c(MAGENTA, "  ! " + warn))
                elif kind == "error":
                    err = str(ev.get("message"))
                    print(c(RED, "  错误：" + err))
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
        if attached:
            state["attached"] = attached
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
        if p.get("vision"):
            bits.append("看图")
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
            snippet = wrap(item["snippet"])[:400].replace("\n", "\n   ")
            print(c(DIM, "   " + snippet))


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
  /providers       列出提供商与模型（带「看图」标记）
  /skills          列出所有技能
  /skill 名字       载入技能到当前会话
  /search 关键词    查搜索服务
  /image 提示词     生成图片
  /video 提示词     生成视频
  /md 路径          渲染 workspace 里的 Markdown
  /file 路径        看纯文本
  /see 图片路径     把本地图片发给模型看图
  /sessions        列出会话，输编号切换
  /session         看当前 session id
  /quit            退出
其他输入直接发给 Agent。Ctrl+C 中断当前显示（不中断服务端任务）。"""


COMMANDS = [
    ("/help", "命令列表"),
    ("/model", "选模型（真实列表，编号即切）"),
    ("/providers", "看提供商与模型"),
    ("/whoami", "看当前账号 / 身份来源"),
    ("/skills", "列出所有技能"),
    ("/skill", "载入技能：/skill 名字"),
    ("/search", "搜网：/search 黄金价格"),
    ("/image", "出图：/image 一只猫"),
    ("/video", "出片：/video 海浪慢镜头"),
    ("/md", "渲染 Markdown：/md 笔记.md"),
    ("/file", "看文本：/file 路径"),
    ("/open", "挂 PDF/Word：/open 文件路径"),
    ("/see", "发图给模型：/see 图片路径"),
    ("/say", "合成语音：/say 你好"),
    ("/cron", "定时任务：/cron 看列表，输编号跑一次"),
    ("/new", "开新会话"),
    ("/sessions", "列出会话并切换"),
    ("/session", "看当前会话 id"),
    ("/quit", "退出"),
]

_SKILLS = []


def _skill_names(api):
    global _SKILLS
    if not _SKILLS:
        try:
            data = api.get("/api/skills")
            _SKILLS = [str(x.get("name") or "") for x in (data.get("skills") or [])]
        except SystemExit:
            _SKILLS = []
    return [n for n in _SKILLS if n]


def _cands(api, buf):
    if buf.startswith("/skill "):
        return _skill_names(api)
    if buf.startswith("/"):
        head = buf.split(" ")[0]
        return [k for k, _ in COMMANDS if k.startswith(head)]
    return []


def _setup_readline(api):
    try:
        import readline
    except ImportError:
        return

    def comp(text, i):
        cands = _cands(api, readline.get_line_buffer())
        return cands[i] if i < len(cands) else None

    readline.set_completer_delims(" ")
    readline.set_completer(comp)
    readline.parse_and_bind("tab: complete")


def print_cmds():
    print(c(DIM, "命令（打 / 看清单，Tab 补全）："))
    for k, d in COMMANDS:
        print("  " + c(CYAN, k.ljust(11)) + c(DIM, d))


def cmd_whoami(api):
    try:
        data = api.get("/api/auth/me")
    except (RuntimeError, SystemExit) as exc:
        print(c(RED, str(exc)))
        return
    me = data.get("me") or {}
    ident = data.get("identity") or {}
    if ident.get("via") == "token":
        print(c(BOLD, "身份：") + "全局 API Token（管理员通道）")
        return
    if not me or not me.get("email"):
        print(c(DIM, "账号体系没开：这台设备谁打开都能用。"))
        return
    role = "主人（owner）" if me.get("role") == "owner" else "成员"
    via = {"cookie": "网页登录", "key": "API 密钥"}.get(ident.get("via"), ident.get("via") or "—")
    print(c(BOLD, "账号：") + str(me.get("email")) + c(DIM, f"  {role} · 来源 {via}"))
    keys = me.get("keys") or []
    if keys:
        print(c(DIM, f"  这个账号有 {len(keys)} 个 API 密钥"))


def cmd_skills(api):
    data = api.get("/api/skills")
    items = data.get("skills") or []
    if not items:
        print(c(DIM, "没有技能：把 SKILL.md 放进 skills/ 或 workspace/.skills/"))
        return
    for x in items:
        print("  " + c(CYAN, str(x.get("name") or "").ljust(16)) + c(DIM, str(x.get("description") or "")[:70]))
    print(c(DIM, "载入：/skill 名字（写进当前会话系统提示词，长期生效）"))


def cmd_load_skill(api, state, name):
    if not name:
        print(c(RED, "用法：/skill 名字（先 /skills 看列表）"))
        return
    sid = state.get("session") or ""
    if not sid:
        print(c(RED, "先随便说一句话开个会话，再载技能"))
        return
    try:
        api.post("/api/sessions/" + enc_path(sid) + "/skills/" + enc_path(name), {})
    except SystemExit as exc:
        print(c(RED, str(exc)))
        return
    except Exception as exc:  # noqa: BLE001  不再掉一大片 traceback
        print(c(RED, "载入技能失败：" + str(exc)))
        return
    print(c(GREEN, "已载入技能 " + name))


def cmd_say(api, text):
    if not text:
        print(c(RED, "用法：/say 要说的话"))
        return
    try:
        result = api.post("/api/ui/tts/speak", {"text": text})
    except (RuntimeError, SystemExit) as exc:
        print(c(RED, "合成失败：" + str(exc)))
        return
    print(c(GREEN, "已合成 " + str(result.get("bytes")) + " 字节 · 音色 " + str(result.get("voice") or "默认")))
    print(c(DIM, "文件：" + str(result.get("path"))))
    print(c(CYAN, api.base + str(result.get("url"))))
    print(c(DIM, "手机点上面这个地址就能播；或 mpv / ffplay 那个文件"))


def _cron_tasks(api):
    data = api.get("/api/schedules")
    return data.get("tasks") or []


def cmd_cron(api, state, arg):
    arg = (arg or "").strip()
    try:
        tasks = _cron_tasks(api)
    except (RuntimeError, SystemExit) as exc:
        print(c(RED, str(exc)))
        return
    if arg.startswith(("off ", "on ")):
        word, _, idx = arg.partition(" ")
        picked = _pick(tasks, idx)
        if picked is None:
            print(c(RED, "编号不对，先 /cron 看列表"))
            return
        try:
            api.post("/api/schedules/" + picked["id"], {"enabled": word == "on"})
        except (RuntimeError, SystemExit) as exc:
            print(c(RED, str(exc)))
            return
        print(c(GREEN, ("已启用 " if word == "on" else "已暂停 ") + picked["name"]))
        return
    if arg.startswith("del "):
        picked = _pick(tasks, arg[4:])
        if picked is None:
            print(c(RED, "编号不对"))
            return
        try:
            api.delete("/api/schedules/" + picked["id"])
        except (RuntimeError, SystemExit) as exc:
            print(c(RED, str(exc)))
            return
        print(c(GREEN, "已删除 " + picked["name"]))
        return
    if not tasks:
        print(c(DIM, "还没有定时任务：网页「定时任务」里建，或直接跟 Agent 说「每天 9 点帮我…」"))
        return
    for i, t in enumerate(tasks, 1):
        flag = "" if t.get("enabled") else c(DIM, "（暂停）")
        last = t.get("last_status") or "未跑过"
        print(f" {c(DIM, f'[{i}]')} {c(BOLD, t['name'])}{flag}  {c(CYAN, t.get('human') or '')}" +
              c(DIM, f"  下次 {t.get('next_run') or '—'} · 上次 {last}"))
        action = t.get("action") or {}
        what = action.get("prompt") or ("工作流 " + str(action.get("workflow")))
        print(c(DIM, "     " + str(what)[:90]))
    raw = input(c(CYAN, "输编号立刻跑一次（回车跳过）：")).strip()
    if raw and raw.isdigit():
        picked = _pick(tasks, raw)
        if picked:
            print(c(DIM, "跑中…"))
            try:
                res = api.post("/api/schedules/" + picked["id"] + "/run", {"wait": True})
            except (RuntimeError, SystemExit) as exc:
                print(c(RED, str(exc)))
                return
            if res.get("ok"):
                print(c(GREEN, "完成 · " + str(res.get("seconds")) + "s"))
                print(wrap(str(res.get("output") or "")))
            else:
                print(c(RED, "失败：" + str(res.get("error") or res.get("message") or "")))


def _pick(tasks, raw):
    raw = str(raw or "").strip()
    if not raw.isdigit():
        return None
    index = int(raw)
    if 1 <= index <= len(tasks):
        return tasks[index - 1]
    return None


def cmd_sessions(api, state):
    data = api.get("/api/sessions")
    items = [x for x in (data.get("sessions") or []) if x.get("kind", "main") == "main"][:20]
    if not items:
        print(c(DIM, "还没有会话"))
        return
    cur = state.get("session") or ""
    for i, item in enumerate(items, 1):
        mark = "*" if item.get("id") == cur else " "
        title = item.get("title") or "未命名"
        sid = str(item.get("id") or "")
        print(f" {c(DIM, f'[{i}]')}{mark} {c(BOLD, title)}  {c(DIM, sid[-6:] + ' · ' + str(item.get('updated_at') or '')[:16])}")
    raw = input(c(CYAN, "编号切换（回车取消）：")).strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(items)):
        return
    picked = items[int(raw) - 1]
    state["session"] = picked.get("id") or ""
    print(c(GREEN, "已切到 " + str(picked.get("title") or state["session"])))


_LOCAL_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


def cmd_see(api: Api, state: dict, path: str) -> None:
    if not path:
        print(c(RED, "用法：/see 图片路径（例如 /see ~/storage/pictures/shot.jpg）"))
        return
    src = Path(path).expanduser()
    if not src.is_absolute():
        src = Path.cwd() / src
    if not src.exists():
        print(c(RED, f"找不到文件：{src}"))
        return
    mime = _LOCAL_MIME.get(src.suffix.lower())
    if not mime:
        print(c(RED, "只收 png / jpg / jpeg / webp / gif"))
        return
    raw = src.read_bytes()
    if not raw:
        print(c(RED, "文件是空的"))
        return
    if len(raw) > 8 * 1024 * 1024:
        print(c(RED, f"图片 {len(raw) / 1024 / 1024:.1f}MB，超过 8MB，先压一下（或截图小一点）"))
        return
    data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    try:
        res = api.post("/api/ui/upload", {"data_url": data_url, "name": src.stem})
    except (RuntimeError, SystemExit) as exc:
        print(c(RED, str(exc)))
        return
    rel = str(res.get("path") or "")
    if not rel:
        print(c(RED, "上传失败：服务端没返回 path"))
        return
    pending = state.setdefault("attached", [])
    if len(pending) >= 3:
        pending.pop(0)
    pending.append({"path": rel, "name": src.name, "type": "image"})
    print(c(GREEN, f"已挂上 {src.name}（{len(raw) / 1024:.0f} KB）→ {rel}"))
    print(c(DIM, "下一句话自动带上这张图，发完清空；最多 3 张同时挂。"))


_DOC_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}


def cmd_open(api: Api, state: dict, path: str) -> None:
    if not path:
        print(c(RED, "用法：/open 文档路径（pdf / docx / doc）"))
        return
    src = Path(path).expanduser()
    if not src.is_absolute():
        src = Path.cwd() / src
    if not src.exists():
        print(c(RED, f"找不到文件：{src}"))
        return
    mime = _DOC_MIME.get(src.suffix.lower())
    if not mime:
        print(c(RED, "只收 pdf / docx / doc；图片用 /see"))
        return
    raw = src.read_bytes()
    if not raw:
        print(c(RED, "文件是空的"))
        return
    if len(raw) > 12 * 1024 * 1024:
        print(c(RED, f"文件 {len(raw) / 1024 / 1024:.1f}MB，超过 12MB（先删掉无关页或压一下）"))
        return
    data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    try:
        res = api.post("/api/ui/upload", {"data_url": data_url, "name": src.name})
    except (RuntimeError, SystemExit) as exc:
        print(c(RED, str(exc)))
        return
    rel = str(res.get("path") or "")
    if not rel:
        print(c(RED, "上传失败：服务端没返回 path"))
        return
    pending = state.setdefault("attached", [])
    if len(pending) >= 3:
        pending.pop(0)
    pending.append({"path": rel, "name": src.name, "type": "file"})
    print(c(GREEN, f"已挂上 {src.name}（{len(raw) / 1024:.0f} KB）→ {rel}"))
    print(c(DIM, "下一句会自动抽文字 + 把前几页转成图片给模型看；发完清空。"))


def main() -> None:
    parser = argparse.ArgumentParser(description="agent-api 终端界面")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="服务地址")
    parser.add_argument("--token", default="", help="X-API-Token（服务端没设就留空）")
    parser.add_argument("--session", default="", help="接着某个 session 聊")
    args = parser.parse_args()
    api = Api(args.base, args.token)
    state = {"session": args.session}
    _setup_readline(api)

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
        if line == "/":
            print_cmds()
            continue
        if line.startswith("/") and " " not in line:
            hits = [k for k, _ in COMMANDS if k.startswith(line)]
            if line not in hits and len(hits) == 1:
                line = hits[0]
            elif line not in hits and len(hits) > 1:
                print(c(DIM, "有歧义：") + c(CYAN, " / ".join(hits)))
                continue
        if line in {"/quit", "/exit", "exit"}:
            break
        if line == "/help":
            print(HELP)
            continue
        if line == "/skill":
            print(c(RED, "用法：/skill 名字（先 /skills 看列表）"))
            continue
        if line == "/whoami":
            cmd_whoami(api)
            continue
        if line == "/skills":
            cmd_skills(api)
            continue
        if line.startswith("/skill "):
            cmd_load_skill(api, state, line[len("/skill "):].strip())
            continue
        if line == "/new":
            state["session"] = ""
            print(c(DIM, "已开新会话。"))
            continue
        if line == "/sessions":
            try:
                cmd_sessions(api, state)
            except (RuntimeError, SystemExit) as exc:
                print(c(RED, str(exc)))
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
        if line.startswith("/open"):
            cmd_open(api, state, line[len("/open"):].strip())
            continue
            continue
        if line == "/see":
            print(c(RED, "用法：/see 图片路径"))
            continue
        if line.startswith("/see "):
            cmd_see(api, state, line[len("/see "):].strip())
            continue
        if line == "/say":
            print(c(RED, "用法：/say 要说的话"))
            continue
        if line.startswith("/say "):
            cmd_say(api, line[len("/say "):].strip())
            continue
        if line == "/cron":
            cmd_cron(api, state, "")
            continue
        if line.startswith("/cron "):
            cmd_cron(api, state, line[len("/cron "):].strip())
            continue
        stream_chat(api, line, [], state)


if __name__ == "__main__":
    main()
