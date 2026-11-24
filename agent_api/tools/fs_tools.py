"""文件系统工具：读、写、改、列目录、搜索，全部限制在沙箱目录内。"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import ToolError
from ..security import active_root
from ..security import display_path, resolve_in_sandbox
from ..utils import truncate
from .base import ToolContext, ToolRegistry

MAX_READ_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {".git", ".svn", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}


def _need(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if value is None or str(value).strip() == "":
        raise ToolError(f"缺少参数 {key}")
    return str(value)


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return False


def read_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = resolve_in_sandbox(_need(args, "path"))
    if not path.exists():
        raise ToolError(f"文件不存在：{display_path(path)}")
    if path.is_dir():
        raise ToolError(f"{display_path(path)} 是目录，请用 list_dir")
    size = path.stat().st_size
    if size > MAX_READ_BYTES:
        raise ToolError(f"文件过大（{size} 字节），请用 offset/limit 分段读取")
    if _is_binary(path):
        raise ToolError(f"{display_path(path)} 看起来是二进制文件，无法按文本读取")
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    offset = max(1, int(args.get("offset") or 1))
    limit = max(1, int(args.get("limit") or 500))
    start = min(offset - 1, len(lines))
    end = min(start + limit, len(lines))
    body = "\n".join(f"{i + 1}\t{lines[i]}" for i in range(start, end))
    header = (
        f"[read_file] {display_path(path)} | 总行数={len(lines)} | 展示 {start + 1}-{end} 行"
        f" | 沙箱根={active_root()}"
    )
    result = f"{header}\n{body}"
    if end < len(lines):
        result += f"\n...（还有 {len(lines) - end} 行，可调 offset 继续读）"
    return truncate(result, settings.max_output_chars)


def write_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = resolve_in_sandbox(_need(args, "path"))
    content = args.get("content")
    if content is None:
        raise ToolError("缺少参数 content")
    if not isinstance(content, str):
        content = str(content)
    append = bool(args.get("append"))
    quota_mb = int(ctx.extra.get("quota_mb") or (os.getenv("GUEST_QUOTA_MB") if ctx.extra.get("guest") else 0) or 0)
    if quota_mb:
        limit = quota_mb * 1024 * 1024
        used = 0
        for item in active_root().rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    used += item.stat().st_size
            except OSError:
                continue
        if used + len(content.encode("utf-8")) > limit:
            raise ToolError(f"空间只有 {limit // 1024 // 1024}MB，已用 {used / 1024 / 1024:.1f}MB，这次写不下了：先删点自己的文件，或升级套餐")
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(content)
    else:
        path.write_text(content, encoding="utf-8")
    return f"OK: 已{'追加' if append else '写入'} {display_path(path)}，本次 {len(content)} 字符，文件共 {path.stat().st_size} 字节"


def edit_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = resolve_in_sandbox(_need(args, "path"))
    if not path.exists():
        raise ToolError(f"文件不存在：{display_path(path)}")
    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or not old:
        raise ToolError("old_string 必须是非空字符串")
    if not isinstance(new, str):
        raise ToolError("new_string 必须是字符串")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ToolError("未找到 old_string，未做任何修改")
    replace_all = bool(args.get("replace_all"))
    if count > 1 and not replace_all:
        raise ToolError(f"old_string 出现了 {count} 次，请提供更长上下文，或设置 replace_all=true")
    path.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1), encoding="utf-8")
    return f"OK: 已修改 {display_path(path)}，替换 {count if replace_all else 1} 处"


def list_dir(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = resolve_in_sandbox(str(args.get("path") or "."))
    if not path.exists():
        raise ToolError(f"路径不存在：{display_path(path)}")
    if not path.is_dir():
        raise ToolError(f"{display_path(path)} 不是目录")
    recursive = bool(args.get("recursive"))
    max_depth = max(1, min(int(args.get("max_depth") or 3), 6))
    lines = [f"[list_dir] {display_path(path)}{' (递归)' if recursive else ''}"]

    def walk(current: Path, prefix: str, depth: int) -> None:
        try:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError as exc:
            lines.append(f"{prefix}! 无法读取：{exc}")
            return
        for entry in entries:
            if entry.name in SKIP_DIRS:
                continue
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                if recursive and depth < max_depth:
                    walk(entry, prefix + "  ", depth + 1)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{prefix}{entry.name}  ({size}B)")

    walk(path, "", 1)
    if len(lines) == 1:
        lines.append("（空目录）")
    return truncate("\n".join(lines), settings.max_output_chars)


def make_dir(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = resolve_in_sandbox(_need(args, "path"))
    path.mkdir(parents=True, exist_ok=True)
    return f"OK: 目录已创建 {display_path(path)}"


def move_path(ctx: ToolContext, args: dict[str, Any]) -> str:
    src = resolve_in_sandbox(_need(args, "src"))
    dst = resolve_in_sandbox(_need(args, "dst"))
    if not src.exists():
        raise ToolError(f"源路径不存在：{display_path(src)}")
    if dst.exists() and not bool(args.get("overwrite")):
        raise ToolError(f"目标已存在：{display_path(dst)}（如需覆盖请传 overwrite=true）")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"OK: {display_path(src)} -> {display_path(dst)}"


def delete_path(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = resolve_in_sandbox(_need(args, "path"))
    if path == active_root():
        raise ToolError("不能删除沙箱根目录")
    if not path.exists():
        raise ToolError(f"路径不存在：{display_path(path)}")
    if path.is_dir():
        if not bool(args.get("recursive")):
            raise ToolError("删除目录需要显式传 recursive=true")
        shutil.rmtree(path)
    else:
        path.unlink()
    return f"OK: 已删除 {display_path(path)}"


def grep_files(ctx: ToolContext, args: dict[str, Any]) -> str:
    pattern = _need(args, "pattern")
    path = resolve_in_sandbox(str(args.get("path") or "."))
    glob_pattern = str(args.get("glob") or "*")
    ignore_case = bool(args.get("ignore_case", True))
    max_results = max(1, min(int(args.get("max_results") or 50), 200))
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ToolError(f"正则不合法：{exc}") from exc
    if not path.exists():
        raise ToolError(f"路径不存在：{display_path(path)}")

    if path.is_file():
        files = [path]
    else:
        files = []
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                candidate = Path(root) / name
                if glob_pattern != "*" and not candidate.match(glob_pattern):
                    continue
                files.append(candidate)

    hits: list[str] = []
    for file in files:
        try:
            if file.stat().st_size > 2 * 1024 * 1024 or _is_binary(file):
                continue
            with file.open("r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    if regex.search(line):
                        hits.append(f"{display_path(file)}:{lineno}: {line.rstrip()[:300]}")
                        if len(hits) >= max_results:
                            hits.append(f"...（已达 {max_results} 条上限）")
                            return "\n".join(hits)
        except OSError:
            continue
    if not hits:
        return f"未匹配到 pattern={pattern}（glob={glob_pattern}，path={display_path(path)}）"
    return "\n".join(hits)


def register(registry: ToolRegistry) -> None:
    registry.register(
        "read_file",
        "读取沙箱目录内的文本文件，返回带行号的内容。大文件用 offset/limit 分段读。",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对沙箱根的路径，例如 src/main.py"},
                "offset": {"type": "integer", "description": "起始行号，从 1 开始"},
                "limit": {"type": "integer", "description": "最多读取多少行，默认 500"},
            },
            "required": ["path"],
        },
        read_file,
    )
    registry.register(
        "write_file",
        "把文本内容写入文件（默认覆盖，append=true 时追加）。父目录不存在会自动创建。",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "append": {"type": "boolean", "default": False},
            },
            "required": ["path", "content"],
        },
        write_file,
    )
    registry.register(
        "edit_file",
        "对文件做精确字符串替换。old_string 必须唯一（或显式 replace_all=true）。",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            },
            "required": ["path", "old_string", "new_string"],
        },
        edit_file,
    )
    registry.register(
        "list_dir",
        "列目录内容，可递归（max_depth 默认 3）。",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "recursive": {"type": "boolean", "default": False},
                "max_depth": {"type": "integer", "default": 3},
            },
        },
        list_dir,
    )
    registry.register(
        "make_dir",
        "创建目录（含多级父目录）。",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        make_dir,
    )
    registry.register(
        "move_path",
        "移动/重命名文件或目录。目标已存在时需 overwrite=true。",
        {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "dst": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["src", "dst"],
        },
        move_path,
    )
    registry.register(
        "delete_path",
        "删除文件或目录（目录必须 recursive=true）。慎用。",
        {"type": "object", "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean", "default": False}},
         "required": ["path"]},
        delete_path,
    )
    registry.register(
        "grep_files",
        "在沙箱内按正则搜索文件内容，返回 path:line: 内容。",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python 正则"},
                "path": {"type": "string", "default": "."},
                "glob": {"type": "string", "default": "*", "description": "文件名通配，如 *.py"},
                "ignore_case": {"type": "boolean", "default": True},
                "max_results": {"type": "integer", "default": 50},
            },
            "required": ["pattern"],
        },
        grep_files,
    )
