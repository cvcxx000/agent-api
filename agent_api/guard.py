"""高危操作闸门（授权确认）。

设计原则：不是一刀切禁掉，也不是静默放行 —— 命中危险模式时先拒一次，把「为什么危险」
和「确认方式」说清楚；用户明确同意后，模型带 `confirm: true` 重调即可放行。

关掉这层闸门：`.env` 里 `DANGEROUS_CONFIRM=false`（不建议）。
"""

from __future__ import annotations

import re
from typing import Any

# 每条：(正则, 人话说明)。命中任意一条即视为高危。
_RULES: list[tuple[str, str]] = [
    (
        r"\brm\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+(?:/\*?|~/?|\$HOME/?)(?=\s|$|[;&|])",
        "递归删除根目录 / 家目录",
    ),
    (
        r"\brm\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+/(?:etc|data|system|sdcard|storage|home|usr|bin|var|proc|sys)(?:/|\s|$)",
        "递归删除系统目录",
    ),
    (r"\bmkfs(\.[a-z0-9]+)?\b", "格式化文件系统"),
    (r"\bdd\b[^|;&]*\bof=/dev/", "直接写裸设备"),
    (r">\s*/dev/(sd|block|mmcblk|nvme)", "覆盖块设备"),
    (r"\b(curl|wget)\b[^|;&]*\|\s*(sh|bash|zsh|python3?)\b", "把网上下载的脚本直接执行"),
    (r"\bchmod\s+-[a-zA-Z]*R[a-zA-Z]*\s+777\s+/", "把根目录权限改成 777"),
    (r"\bchown\s+-[a-zA-Z]*R[a-zA-Z]*\s+\S+\s+/", "递归改根目录属主"),
    (r"\b(sudo|doas|su\s+-)\b", "提权执行"),
    (
        r"\b(apt|apt-get|pkg|apk|dnf|yum|pacman)\s+(remove|purge|uninstall|del)\b",
        "卸载系统软件包",
    ),
    (r"\bkill\s+-9\s+-1\b|\bkillall5\b|\bpkill\s+-9\b", "杀全部进程"),
    (r":\s*\(\s*\)\s*\{.*\|\s*:\s*&\s*\}", "fork 炸弹"),
    (r"\b(reboot|shutdown|halt|poweroff)\b", "重启 / 关机"),
    (r"\bgit\s+push\s+(--force|-f)\b|\bgit\s+reset\s+--hard\b", "强推 / 丢弃未提交改动"),
    (r"\bmv\s+\S+\s+/dev/null\b", "把文件丢进 /dev/null"),
    (r"\bfind\s+/\s+.*-delete\b", "全盘删除匹配文件"),
    (r"open\(\s*['\"]/(etc|data|system|sdcard)", "写系统路径"),
    (r"\bshutil\.rmtree\(\s*['\"]/(\s*['\"]|\*)|rmtree\(\s*['\"]~", "Python 递归删目录"),
    (r"\bos\.(system|popen)\(\s*['\"]rm\s+-rf", "Python 里执行 rm -rf"),
    (r"subprocess\.\w+\(\s*\[?\s*['\"]rm['\"]", "Python 里调 rm"),
]

_COMPILED = [(re.compile(pat), why) for pat, why in _RULES]


def check(text: str) -> list[str]:
    """返回命中的高危原因列表（空列表 = 放行）。"""
    body = str(text or "")
    if not body.strip():
        return []
    hits: list[str] = []
    for pattern, why in _COMPILED:
        if pattern.search(body) and why not in hits:
            hits.append(why)
    return hits


def refusal(tool: str, text: str, hits: list[str], preview: int = 200) -> str:
    """给模型看的拒绍文案：说清危险点 + 明确的重试方式。"""
    shown = str(text or "")
    if len(shown) > preview:
        shown = shown[:preview] + " …"
    reasons = "；".join(hits)
    return (
        f"高危操作已拦截（{tool}）：{reasons}\n"
        f"内容：{shown}\n"
        "这是一次授权确认，不是失败。请先把上面这条命令/代码要做的事（会删什么、会改什么、"
        "会不会影响系统）用一两句话告诉用户，得到用户明确同意后，带 confirm=true 再调用一次；"
        "用户没同意就不要重试，可以换个更安全的做法。"
    )


def enabled(flag: Any = None) -> bool:
    """闸门开关：flag 显式给出优先，否则看环境变量 DANGEROUS_CONFIRM（默认开）。"""
    if isinstance(flag, bool):
        return flag
    import os

    raw = str(os.getenv("DANGEROUS_CONFIRM", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}
