"""代码执行工具：在沙箱目录里跑 Python /（可选）shell 命令。"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

from .. import guard
from ..config import settings
from ..errors import ToolError
from ..utils import new_id
from .base import ToolContext, ToolRegistry

MAX_CODE_CHARS = 200_000


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(settings.sandbox_root)
    env.pop("PYTHONSTARTUP", None)
    return env


def _run(cmd: list[str], timeout: float) -> tuple[int, str, str, float]:
    started = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(settings.sandbox_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_runtime_env(),
        start_new_session=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - started
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        stdout, stderr = proc.communicate()
        raise ToolError(
            f"执行超时（>{timeout:g}s），进程已被强制结束（耗时 {elapsed:.2f}s）。\n"
            f"{_format_output(stdout, stderr)}"
        )
    elapsed = time.perf_counter() - started
    return proc.returncode, stdout or "", stderr or "", elapsed


def _format_output(stdout: str, stderr: str) -> str:
    parts = []
    if stdout.strip():
        parts.append(f"--- stdout ---\n{stdout.rstrip()}")
    if stderr.strip():
        parts.append(f"--- stderr ---\n{stderr.rstrip()}")
    return "\n".join(parts) or "（无输出）"


def run_python(ctx: ToolContext, args: dict[str, Any]) -> str:
    if not settings.enable_python:
        raise ToolError("服务端已关闭 Python 执行（ENABLE_PYTHON=false）")
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ToolError("code 不能为空")
    if len(code) > MAX_CODE_CHARS:
        raise ToolError(f"代码过长（>{MAX_CODE_CHARS} 字符），请拆分或写到文件里再运行")
    hits = guard.check(code, root=settings.sandbox_root)
    if hits and not args.get("confirm"):
        raise ToolError(guard.refusal("run_python", code, hits))
    timeout = max(1, min(int(args.get("timeout") or settings.python_timeout), 600))

    (settings.data_dir / "tmp").mkdir(parents=True, exist_ok=True)
    script = settings.data_dir / "tmp" / f"run_{new_id('x')}.py"
    script.write_text(code, encoding="utf-8")
    try:
        exit_code, stdout, stderr, elapsed = _run([sys.executable, "-u", str(script)], timeout)
    finally:
        script.unlink(missing_ok=True)

    header = f"[run_python] exit={exit_code} time={elapsed:.2f}s timeout={timeout}s"
    return header + "\n" + _format_output(stdout, stderr)


def _shell_path() -> str:
    """找 shell。Termux/Android 上没有 /bin/bash（只有 $PREFIX/bin/bash），所以不能写死。"""
    return shutil.which("bash") or shutil.which("sh") or "/bin/sh"


def run_shell(ctx: ToolContext, args: dict[str, Any]) -> str:
    if not settings.enable_shell:
        raise ToolError("服务端未启用 shell（需设置 ENABLE_SHELL=true）")
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolError("command 不能为空")
    hits = guard.check(command, root=settings.sandbox_root)
    if hits and not args.get("confirm"):
        raise ToolError(guard.refusal("run_shell", command, hits))
    timeout = max(1, min(int(args.get("timeout") or settings.shell_timeout), 600))
    exit_code, stdout, stderr, elapsed = _run([_shell_path(), "-lc", command], timeout)
    return f"[run_shell] exit={exit_code} time={elapsed:.2f}s\n" + _format_output(stdout, stderr)


def register(registry: ToolRegistry) -> None:
    registry.register(
        "run_python",
        "在沙箱目录（cwd 为工作区根）用当前 Python 解释器执行一段代码，返回 exit code / stdout / stderr。"
        "适合做计算、处理文件、写脚本验证；超时会强制结束。"
        "看图片别用它：用 view_image，让模型真的看到画面，而不是读像素去猜。"
        "删系统目录、调 subprocess 删文件这类高危代码会被拦一次，需要用户同意后带 confirm=true。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "完整 Python 代码"},
                "timeout": {"type": "integer", "description": f"秒，默认 {settings.python_timeout}，上限 600"},
                "confirm": {"type": "boolean", "description": "被高危闸门拦下、且用户已明确同意时传 true"},
            },
            "required": ["code"],
        },
        run_python,
    )
    registry.register(
        "run_shell",
        "执行 shell 命令（需 ENABLE_SHELL=true）。cwd 是 workspace，bash/sh 自动探测（兼容 Termux）。"
        "高危不只系统目录：一次删 ≥100 个文件、或动到 uploads/outputs/media/data/plugins 也会被拦一次，"
        "先把影响说给用户，用户同意后带 confirm=true 重调。",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": f"秒，默认 {settings.shell_timeout}，上限 600"},
                "confirm": {"type": "boolean", "description": "被高危闸门拦下、且用户已明确同意时传 true"},
            },
            "required": ["command"],
        },
        run_shell,
    )
