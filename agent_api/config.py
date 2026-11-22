"""集中配置：读取 .env / 环境变量，不依赖任何第三方库。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    path = BASE_DIR / ".env"
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


_load_env_file()


def _str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip()


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key) or default)
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key) or default)
    except ValueError:
        return default


def _bool(key: str, default: bool = False) -> bool:
    raw = _str(key).lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _path(key: str, default: Path) -> Path:
    raw = _str(key)
    return _resolve(raw) if raw else Path(default).resolve()


def _paths(key: str, default: list[Path]) -> tuple[Path, ...]:
    raw = _str(key)
    if not raw:
        return tuple(Path(p).resolve() for p in default)
    return tuple(_resolve(item.strip()) for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    # ---- 服务 ----
    app_name: str
    host: str
    port: int
    api_token: str
    # ---- 大模型（OpenAI 兼容）----
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    llm_max_tokens: int
    llm_timeout: float
    llm_mock: str
    # ---- Agent 行为 ----
    max_turns: int
    history_limit: int
    max_subagents: int
    max_subagent_depth: int
    max_output_chars: int
    # ---- 代码执行 ----
    python_timeout: int
    shell_timeout: int
    enable_python: bool
    enable_shell: bool
    # ---- 目录 ----
    sandbox_root: Path
    data_dir: Path
    skills_dirs: tuple[Path, ...]
    workflows_dir: Path

    @property
    def use_mock_llm(self) -> bool:
        return self.llm_mock == "true" or (self.llm_mock == "auto" and not self.llm_api_key)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_token)

    @classmethod
    def from_env(cls) -> "Settings":
        sandbox = _path("SANDBOX_ROOT", BASE_DIR / "workspace")
        return cls(
            app_name=_str("APP_NAME", "agent-api"),
            host=_str("HOST", "0.0.0.0"),
            port=_int("PORT", 8000),
            api_token=_str("API_TOKEN", ""),
            llm_base_url=_str("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_api_key=_str("LLM_API_KEY", ""),
            llm_model=_str("LLM_MODEL", "gpt-4o-mini"),
            llm_temperature=_float("LLM_TEMPERATURE", 0.3),
            llm_max_tokens=_int("LLM_MAX_TOKENS", 4096),
            llm_timeout=_float("LLM_TIMEOUT", 120.0),
            llm_mock=_str("LLM_MOCK", "auto").lower(),
            max_turns=_int("AGENT_MAX_TURNS", 16),
            history_limit=_int("AGENT_HISTORY_LIMIT", 80),
            max_subagents=_int("AGENT_MAX_SUBAGENTS", 3),
            max_subagent_depth=_int("AGENT_MAX_SUBAGENT_DEPTH", 1),
            max_output_chars=_int("MAX_TOOL_OUTPUT_CHARS", 20000),
            python_timeout=_int("PYTHON_TIMEOUT", 60),
            shell_timeout=_int("SHELL_TIMEOUT", 60),
            enable_python=_bool("ENABLE_PYTHON", True),
            enable_shell=_bool("ENABLE_SHELL", True),
            sandbox_root=sandbox,
            data_dir=_path("DATA_DIR", BASE_DIR / "data"),
            skills_dirs=_paths("SKILLS_DIRS", [BASE_DIR / "skills", sandbox / ".skills"]),
            workflows_dir=_path("WORKFLOWS_DIR", BASE_DIR / "workflows"),
        )


settings = Settings.from_env()
