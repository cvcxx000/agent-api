"""安全：路径沙箱 + 请求鉴权。"""

from __future__ import annotations

import contextvars
from pathlib import Path

from .config import settings

_ACTIVE_ROOT: contextvars.ContextVar[Path | None] = contextvars.ContextVar("agent_sandbox_root", default=None)


def active_root() -> Path:
    """当前请求的沙箱根：游客请求会被换成他自己的目录，普通账号就是 settings.sandbox_root。"""
    raw = _ACTIVE_ROOT.get()
    return Path(raw).resolve() if raw else Path(settings.sandbox_root).resolve()


def use_root(root: Path | str | None) -> contextvars.Token:
    return _ACTIVE_ROOT.set(Path(root) if root else None)


def reset_root(token: contextvars.Token) -> None:
    _ACTIVE_ROOT.reset(token)
from .errors import AppError


class PathEscapeError(AppError):
    status_code = 400
    code = "path_outside_sandbox"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


def resolve_in_sandbox(raw_path: str, root: Path | None = None) -> Path:
    """把路径限制在沙箱内，阻止 ../ 与软链接逃逸。"""
    root = Path(root).resolve() if root else active_root()
    if raw_path is None or str(raw_path).strip() == "":
        raise PathEscapeError("path 不能为空")
    path = Path(str(raw_path).strip()).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise PathEscapeError(f"路径无法解析：{raw_path}（{exc}）") from exc
    if resolved != root and root not in resolved.parents:
        raise PathEscapeError(f"路径越界：{raw_path} 不在沙箱目录 {root} 内")
    return resolved


def display_path(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(active_root())).replace("\\", "/") or "."
    except ValueError:
        return str(path)


def check_token(token: str | None) -> None:
    if not settings.auth_enabled:
        return
    if not token or token != settings.api_token:
        raise AuthError("鉴权失败：请在请求头带上正确的 X-API-Token")
