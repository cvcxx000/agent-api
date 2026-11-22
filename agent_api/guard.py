"""高危操作闸门（授权确认）。

设计原则：不是一刀切禁掉，也不是静默放行 —— 命中危险模式时先拒一次，把「为什么危险」
和「确认方式」说清楚；用户明确同意后，模型带 `confirm: true` 重调即可放行。

三层判断：
1. 静态规则：系统目录、提权、卸载、强推、清空文件……
2. 破坏面估算：按真实文件系统数一下会删多少文件（≥100 要确认）
3. 受保护目录：uploads / outputs / media / data / plugins / skills / .git 不能被一把梭清掉

关掉这层闸门：`.env` 里 `DANGEROUS_CONFIRM=false`（不建议）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
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
    (r"\bgit\s+clean\s+-[a-zA-Z]*[dx]", "git clean 会删掉未跟踪文件"),
    (r"\btruncate\s+-s\s*0\b|>\s*\S+\s*$", "清空/覆盖文件"),
    (r"\bfind\s+/\s+.*-delete\b", "全盘删除匹配文件"),
    (r"open\(\s*['\"]/(etc|data|system|sdcard)", "写系统路径"),
    (r"\bshutil\.rmtree\(\s*['\"]/(\s*['\"]|\*)|rmtree\(\s*['\"]~", "Python 递归删目录"),
    (r"\bos\.(system|popen)\(\s*['\"]rm\s+-rf", "Python 里执行 rm -rf"),
    (r"subprocess\.\w+\(\s*\[?\s*['\"]rm['\"]", "Python 里调 rm"),
]

_COMPILED = [(re.compile(pat), why) for pat, why in _RULES]

# 工作目录里这些子目录不允许被一把梭清掉（产物、上传、插件、数据）
_PROTECTED_NAMES = {
    "uploads",
    "outputs",
    "media",
    "data",
    "plugins",
    "skills",
    ".git",
    ".skills",
    ".plugins",
    "workflows",
}

_WILD_DELETE_RE = re.compile(r"\brm\s+(?:-[a-zA-Z]+\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+(?:\*|\./?|\*/\*?)(?=\s|$|[;&|])")
_RM_ARGS_RE = re.compile(r"\brm\s+((?:-[A-Za-z]+\s+)*)([^;&|]+)")
_FIND_DELETE_RE = re.compile(r"\bfind\s+([^;&|]*?)\s+-delete\b")

_SCAN_LIMIT = 5000
_MASS_LIMIT = 100


def _count(path: Path) -> int:
    """数一个目标会连累多少文件（目录就递归数，超过上限就停）。"""
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    total = 0
    for _ in path.rglob("*"):
        total += 1
        if total >= _SCAN_LIMIT:
            break
    return total


def _delete_targets(text: str) -> list[str]:
    """粗抽出这条命令要删哪些路径（rm -rf 的参数 / find X -delete）。"""
    targets: list[str] = []
    for match in _RM_ARGS_RE.finditer(text):
        flags = match.group(1) or ""
        if not re.search(r"-[A-Za-z]*[rf][A-Za-z]*", flags):
            continue
        for token in match.group(2).split():
            if token.startswith("-") or token in {"&&", "||"}:
                continue
            targets.append(token)
    for match in _FIND_DELETE_RE.finditer(text):
        first = (match.group(1) or "").split()
        if first and not first[0].startswith("-"):
            targets.append(first[0])
    return targets


def _blast(text: str, root: Path) -> list[str]:
    """按真实文件系统估算破坏面：动了受保护目录、或一次删太多文件，都要确认。"""
    reasons: list[str] = []
    for raw in _delete_targets(text):
        target = (root / raw).resolve() if not os.path.isabs(raw) else Path(raw)
        try:
            target.relative_to(root)
            inside = True
        except ValueError:
            inside = False
        if inside and target == root:
            reasons.append("会把整个工作目录清空")
            continue
        parts = {p.lower() for p in target.parts}
        for name in _PROTECTED_NAMES & parts:
            reason = f"动到受保护目录 {name}/（上传/产物/插件/数据）"
            if reason not in reasons:
                reasons.append(reason)
        count = _count(target)
        if count >= _MASS_LIMIT:
            reason = f"一次会删掉约 {count} 个文件"
            if reason not in reasons:
                reasons.append(reason)
    return reasons


def check(text: str, root: str | Path | None = None) -> list[str]:
    """返回命中的高危原因列表（空列表 = 放行）。root 给了就顺带做破坏面估算。"""
    body = str(text or "")
    if not body.strip():
        return []
    hits: list[str] = []
    for pattern, why in _COMPILED:
        if pattern.search(body) and why not in hits:
            hits.append(why)
    if _WILD_DELETE_RE.search(body) and "删除当前目录或通配符（*. 会被整个清空）" not in hits:
        hits.append("删除当前目录或通配符（*. 会被整个清空）")
    if root is not None:
        try:
            base = Path(root).resolve()
            for reason in _blast(body, base):
                if reason not in hits:
                    hits.append(reason)
        except Exception:  # noqa: BLE001 - 估算失败不该把闸门带崩
            pass
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
    raw = str(os.getenv("DANGEROUS_CONFIRM", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}
