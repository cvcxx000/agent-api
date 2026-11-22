"""代码执行工具：在沙箱目录里跑 Python /（可选）shell 命令。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any

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


def run_shell(ctx: ToolContext, args: dict[str, Any]) -> str:
    if not settings.enable_shell:
        raise ToolError("服务端未启用 shell（需设置 ENABLE_SHELL=true）")
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolError("command 不能为空")
    timeout = max(1, min(int(args.get("timeout") or settings.shell_timeout), 600))
    exit_code, stdout, stderr, elapsed = _run(["/bin/bash", "-lc", command], timeout)
    return f"[run_shell] exit={exit_code} time={elapsed:.2f}s\n" + _format_output(stdout, stderr)


def register(registry: ToolRegistry) -> None:
    registry.register(
        "run_python",
        "在沙箱目录（cwd 为工作区根）用当前 Python 解释器执行一段代码，返回 exit code / stdout / stderr。"
        "适合做计算、处理文件、写脚本验证；超时会强制结束。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "完整 Python 代码"},
                "timeout": {"type": "integer", "description": f"秒，默认 {settings.python_timeout}，上限 600"},
            },
            "required": ["code"],
        },
        run_python,
    )
    registry.register(
        "run_shell",
        "执行 shell 命令（默认关闭，需 ENABLE_SHELL=true）。",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": f"秒，默认 {settings.shell_timeout}"},
            },
            "required": ["command"],
        },
        run_shell,
    )
