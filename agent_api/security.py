"""安全：路径沙箱 + 请求鉴权。"""

from __future__ import annotations

from pathlib import Path

from .config import settings
from .errors import AppError


class PathEscapeError(AppError):
    status_code = 400
    code = "path_outside_sandbox"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


def resolve_in_sandbox(raw_path: str, root: Path | None = None) -> Path:
    """把路径限制在沙箱内，阻止 ../ 与软链接逃逸。"""
    root = Path(root or settings.sandbox_root).resolve()
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
        return str(Path(path).resolve().relative_to(settings.sandbox_root)).replace("\\", "/") or "."
    except ValueError:
        return str(path)


def check_token(token: str | None) -> None:
    if not settings.auth_enabled:
        return
    if not token or token != settings.api_token:
        raise AuthError("鉴权失败：请在请求头带上正确的 X-API-Token")
