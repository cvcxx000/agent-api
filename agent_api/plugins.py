"""万物皆插件：从 `plugins/` 和 `workspace/.plugins/` 动态装载工具。

一个插件就是一个 .py（或 <name>/__init__.py）文件，两种写法都行：

    PLUGIN = {"name": "word_count", "description": "统计字数"}

    def register(registry):
        registry.register("word_count", "...", {...}, handler)

    # 或者直接给工具清单：
    TOOLS = [
        {"name": "word_count", "description": "...", "parameters": {...}, "handler": handler},
    ]

加载规则（故意这样的）：
- 启动时只装「已启用」的插件；新掉进目录的插件默认是停着的。
- 启用 = 执行任意代码，必须先拿到用户同意（plugin_enable 要 confirm=true）。
- 单个插件报错只记在这个插件上，不影响服务启动，也不影响别的插件。
- 重载时先把上次注册的工具撤掉，避免重复注册。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from .config import settings
from .errors import AppError, NotFoundError

logger = logging.getLogger("agent.plugins")

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


class PluginManager:
    def __init__(self, st: Any = None) -> None:
        self.st = st or settings
        root = Path(__file__).resolve().parent.parent
        self.dirs: list[Path] = [root / "plugins", Path(self.st.sandbox_root) / ".plugins"]
        for one in self.dirs:
            one.mkdir(parents=True, exist_ok=True)
        self.state_path = Path(self.st.data_dir) / "plugins.json"
        self.registry: Any = None
        self._loaded: dict[str, list[str]] = {}

    # ---------- 状态 ----------
    def _state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("enabled", [])
                    data.setdefault("records", {})
                    return data
            except (OSError, json.JSONDecodeError):
                logger.warning("plugins.json 坏了，按空处理", exc_info=True)
        return {"enabled": [], "records": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ---------- 发现 ----------
    def paths(self) -> list[Path]:
        out: list[Path] = []
        for directory in self.dirs:
            if not directory.exists():
                continue
            for item in sorted(directory.iterdir()):
                if item.name.startswith("_") or item.name.startswith("."):
                    continue
                if item.is_file() and item.suffix == ".py":
                    out.append(item)
                elif item.is_dir() and (item / "__init__.py").exists():
                    out.append(item / "__init__.py")
        return out

    def _find(self, name: str) -> Path | None:
        for path in self.paths():
            stem = path.parent.name if path.name == "__init__.py" else path.stem
            if stem == name:
                return path
        return None

    def entries(self) -> list[dict[str, Any]]:
        state = self._state()
        enabled = set(state.get("enabled") or [])
        records = state.get("records") or {}
        out: list[dict[str, Any]] = []
        for path in self.paths():
            name = path.parent.name if path.name == "__init__.py" else path.stem
            record = records.get(name) or {}
            out.append(
                {
                    "name": name,
                    "path": str(path),
                    "description": record.get("description") or "",
                    "enabled": name in enabled,
                    "loaded": name in self._loaded,
                    "tools": list(self._loaded.get(name) or []),
                    "error": record.get("error") or "",
                }
            )
        return out

    def describe(self) -> dict[str, Any]:
        items = self.entries()
        return {
            "dirs": [str(d) for d in self.dirs],
            "count": len(items),
            "enabled": [i["name"] for i in items if i["enabled"]],
            "items": items,
        }

    # ---------- 装载 ----------
    def bind(self, registry: Any) -> None:
        self.registry = registry

    def _import(self, name: str, path: Path) -> Any:
        mod_name = f"agent_plugin_{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        spec = importlib.util.spec_from_file_location(mod_name, str(path))
        if spec is None or spec.loader is None:
            raise AppError(f"插件 {name} 装载失败：读不出模块")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _register_tools(self, name: str, module: Any) -> list[str]:
        if self.registry is None:
            return []
        before = set(self.registry.names())
        if hasattr(module, "register") and callable(module.register):
            module.register(self.registry)
        tools = getattr(module, "TOOLS", None)
        if isinstance(tools, list):
            for spec in tools:
                if not isinstance(spec, dict) or not spec.get("name"):
                    continue
                self.registry.register(
                    str(spec["name"]),
                    str(spec.get("description") or ""),
                    spec.get("parameters") or {"type": "object", "properties": {}},
                    spec.get("handler") or (lambda ctx, args: ""),
                    bool(spec.get("requires_spawn", False)),
                )
        return [n for n in self.registry.names() if n not in before]

    def load_one(self, name: str) -> dict[str, Any]:
        """装一个插件并把结果写进状态（失败也写，便于 plugin_list 看见原因）。"""
        path = self._find(name)
        if path is None:
            raise NotFoundError(f"没有这个插件：{name}")
        state = self._state()
        records = state.setdefault("records", {})
        record = records.setdefault(name, {})
        record.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            module = self._import(name, path)
            meta = getattr(module, "PLUGIN", None)
            desc = str(meta.get("description") or "") if isinstance(meta, dict) else ""
            tools = self._register_tools(name, module)
            self._loaded[name] = tools
            record["description"] = desc or record.get("description") or ""
            record["error"] = ""
            record["tools"] = tools
            record["loaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save(state)
            return {"ok": True, "name": name, "tools": tools}
        except Exception as exc:  # noqa: BLE001 - 插件炸了不能带走主进程
            logger.warning("插件 %s 装载失败：%s", name, exc)
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["traceback"] = traceback.format_exc()[-800:]
            self._save(state)
            return {"ok": False, "name": name, "error": record["error"]}

    def unload_one(self, name: str) -> list[str]:
        removed: list[str] = []
        if self.registry is not None:
            for tool in self._loaded.get(name) or []:
                if self.registry.unregister(tool):
                    removed.append(tool)
        self._loaded.pop(name, None)
        return removed

    def reload(self) -> dict[str, Any]:
        for name in list(self._loaded):
            self.unload_one(name)
        state = self._state()
        enabled = [str(x) for x in (state.get("enabled") or [])]
        results = []
        for name in enabled:
            if self._find(name) is None:
                results.append({"ok": False, "name": name, "error": "文件不见了"})
                continue
            results.append(self.load_one(name))
        return {"ok": all(r.get("ok") for r in results) if results else True, "items": results, **self.describe()}

    def enable(self, name: str) -> dict[str, Any]:
        path = self._find(name)
        if path is None:
            raise NotFoundError(f"没有这个插件：{name}")
        state = self._state()
        enabled = [str(x) for x in (state.get("enabled") or [])]
        if name not in enabled:
            enabled.append(name)
        state["enabled"] = enabled
        self._save(state)
        result = self.load_one(name)
        return {**result, **self.describe()}

    def disable(self, name: str) -> dict[str, Any]:
        removed = self.unload_one(name)
        state = self._state()
        state["enabled"] = [x for x in (state.get("enabled") or []) if x != name]
        self._save(state)
        return {"ok": True, "name": name, "removed_tools": removed, **self.describe()}

    # ---------- 新建 / 改写 ----------
    def _check_name(self, name: str) -> str:
        name = str(name or "").strip().lower()
        if not _NAME_RE.match(name):
            raise AppError("插件名只能是小写字母/数字/下划线，2-40 位，且以字母开头")
        return name

    def create(self, name: str, code: str, description: str = "") -> dict[str, Any]:
        name = self._check_name(name)
        if self._find(name) is not None:
            raise AppError(f"插件 {name} 已存在，改写用 plugin_update")
        code = str(code or "")
        if not code.strip():
            raise AppError("code 不能为空")
        try:
            compile(code, f"{name}.py", "exec")
        except SyntaxError as exc:
            raise AppError(f"代码有语法错误（第 {exc.lineno} 行）：{exc.msg}") from exc
        target = self.dirs[0] / f"{name}.py"
        target.write_text(code, encoding="utf-8")
        state = self._state()
        records = state.setdefault("records", {})
        records[name] = {
            "description": str(description or ""),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": "",
            "tools": [],
        }
        self._save(state)
        return {"ok": True, "name": name, "path": str(target), "enabled": name in (state.get("enabled") or [])}

    def update(self, name: str, code: str) -> dict[str, Any]:
        name = self._check_name(name)
        path = self._find(name)
        if path is None:
            raise NotFoundError(f"没有这个插件：{name}，新建用 plugin_create")
        code = str(code or "")
        if not code.strip():
            raise AppError("code 不能为空")
        try:
            compile(code, str(path), "exec")
        except SyntaxError as exc:
            raise AppError(f"代码有语法错误（第 {exc.lineno} 行）：{exc.msg}") from exc
        was_enabled = name in (self._state().get("enabled") or [])
        path.write_text(code, encoding="utf-8")
        if was_enabled:
            self.unload_one(name)
            return {**self.load_one(name), "reloaded": True, **self.describe()}
        return {"ok": True, "name": name, "reloaded": False, **self.describe()}

    def read(self, name: str) -> dict[str, Any]:
        path = self._find(str(name or "").strip())
        if path is None:
            raise NotFoundError(f"没有这个插件：{name}")
        return {"name": name, "path": str(path), "code": path.read_text(encoding="utf-8")}

    def delete(self, name: str) -> dict[str, Any]:
        path = self._find(str(name or "").strip())
        if path is None:
            raise NotFoundError(f"没有这个插件：{name}")
        self.unload_one(name)
        path.unlink(missing_ok=True)
        state = self._state()
        state["enabled"] = [x for x in (state.get("enabled") or []) if x != name]
        (state.get("records") or {}).pop(name, None)
        self._save(state)
        return {"ok": True, "name": name, "deleted": str(path), **self.describe()}


def load_enabled(manager: PluginManager, registry: Any) -> None:
    manager.bind(registry)
    try:
        manager.reload()
    except Exception:  # noqa: BLE001 - 插件坏了不该挡住启动
        logger.warning("插件初始化失败", exc_info=True)
