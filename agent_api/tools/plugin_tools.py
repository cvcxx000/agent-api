"""给模型用的插件管理工具：列出 / 新建 / 改 / 开关 / 重载 / 删除。

设计意图（「万物皆插件」）：模型遇到自己没有的能力时，可以自己写一个插件装上，
下次同类任务直接调工具，而不是每次都现写临时脚本。写代码不算数，
**启用才算**——启用要用户点头（confirm=true）。
"""

from __future__ import annotations

from typing import Any, Callable

from .. import guard
from .base import ToolContext, ToolRegistry

GetManager = Callable[[], Any]

_SCAFFOLD = '''"""插件：{name} —— {description}"""

PLUGIN = {{"name": "{name}", "description": "{description}"}}


def {func}(ctx, args):
    """工具实现：返回字符串（可直接带 Markdown / MEDIA_IMAGE: 之类标记）。

    路径别写相对路径：要么给绝对路径，要么
    from agent_api.security import resolve_in_sandbox 自己解析到 workspace。
    """
    return "还没实现：这里是 {name} 的入口"


def register(registry):
    registry.register(
        "{name}",
        "{description}",
        {{
            "type": "object",
            "properties": {{"text": {{"type": "string", "description": "输入"}}}},
            "required": ["text"],
        }},
        {func},
    )
'''


def _tools_line(manager: Any) -> str:
    data = manager.describe()
    items = data.get("items") or []
    if not items:
        return f"还没有插件。插件目录：{'、'.join(data.get('dirs') or [])}"
    lines = [f"插件 {len(items)} 个，启用 {len(data.get('enabled') or [])} 个："]
    for item in items:
        flag = "启用" if item.get("enabled") else "停用"
        tools = ", ".join(item.get("tools") or []) or "-"
        line = f"· {item['name']}  [{flag}]  工具：{tools}"
        if item.get("description"):
            line += f"  — {item['description']}"
        if item.get("error"):
            line += f"  ⚠ {item['error'][:120]}"
        lines.append(line)
    return "\n".join(lines)


def register(registry: ToolRegistry, get_manager: GetManager) -> None:
    def plugin_list(ctx: ToolContext, args: dict[str, Any]) -> str:
        manager = get_manager()
        data = manager.describe()
        return _tools_line(manager) + f"\n目录：{'、'.join(data.get('dirs') or [])}"

    def plugin_create(ctx: ToolContext, args: dict[str, Any]) -> str:
        manager = get_manager()
        name = str(args.get("name") or "").strip().lower()
        code = str(args.get("code") or "")
        description = str(args.get("description") or "").strip()
        if not code.strip():
            func = name.replace("-", "_") or "run"
            code = _SCAFFOLD.format(name=name, func=func, description=description or f"{name} 的插件")
        result = manager.create(name, code, description)
        return (
            f"插件已创建：{result['path']}\n"
            f"还**没启用**（新建插件一律先停着，因为它会执行任意代码）。\n"
            f"要用它：先跟用户确认，再调 plugin_enable（name=\"{result['name']}\", confirm=true）。"
        )

    def plugin_update(ctx: ToolContext, args: dict[str, Any]) -> str:
        manager = get_manager()
        result = manager.update(str(args.get("name") or ""), str(args.get("code") or ""))
        state = "已重新加载" if result.get("reloaded") else "还是停用状态，改动等启用后生效"
        return f"插件 {result['name']} 已更新，{state}。"

    def plugin_read(ctx: ToolContext, args: dict[str, Any]) -> str:
        manager = get_manager()
        data = manager.read(str(args.get("name") or ""))
        code = data.get("code") or ""
        if len(code) > 4000:
            code = code[:4000] + "\n…（后面省略，改的时候整段覆盖）"
        return f"{data['path']}\n\n```python\n{code}\n```"

    def plugin_enable(ctx: ToolContext, args: dict[str, Any]) -> str:
        manager = get_manager()
        name = str(args.get("name") or "").strip()
        info = manager.read(name)
        code = info.get("code") or ""
        hits = guard.check(code)
        if hits and not args.get("confirm"):
            return guard.refusal("plugin_enable", code, hits)
        if not args.get("confirm"):
            return (
                f"`plugin_enable` 会执行 {name} 的代码（等于在这台机器上跑任意 Python）。\n"
                "先把插件要做什么、会不会动文件和网络说给用户，用户明确同意后带 confirm=true 再调。"
            )
        result = manager.enable(name)
        if not result.get("ok"):
            return f"插件 {name} 启用失败：{result.get('error')}"
        tools = ", ".join(result.get("tools") or []) or "（没注册工具，看看 register() 里有没有写错）"
        return f"插件 {name} 已启用，新增工具：{tools}\n直接就能调了，不用重启。"

    def plugin_disable(ctx: ToolContext, args: dict[str, Any]) -> str:
        manager = get_manager()
        result = manager.disable(str(args.get("name") or "").strip())
        removed = ", ".join(result.get("removed_tools") or []) or "（本来就没装载）"
        return f"插件 {result['name']} 已停用，撑下的工具已移除：{removed}"

    def plugin_reload(ctx: ToolContext, args: dict[str, Any]) -> str:
        manager = get_manager()
        result = manager.reload()
        bad = [f"{r['name']}：{r.get('error')}" for r in (result.get("items") or []) if not r.get("ok")]
        note = ""
        if bad:
            note = "\n装载失败的：" + "；".join(bad)
        return _tools_line(manager) + note

    def plugin_delete(ctx: ToolContext, args: dict[str, Any]) -> str:
        manager = get_manager()
        name = str(args.get("name") or "").strip()
        if not args.get("confirm"):
            return f"删除插件 {name} 会同时删掉文件并卸载工具。用户同意后带 confirm=true 再调。"
        result = manager.delete(name)
        return f"插件 {result['name']} 已删除：{result.get('deleted')}"

    registry.register(
        "plugin_list",
        "列出所有插件（名字、是否启用、注册了哪些工具、装载错误）。想知道「有没有现成能力」先看这个。",
        {"type": "object", "properties": {}},
        plugin_list,
    )
    registry.register(
        "plugin_create",
        "写一个新插件（一个 .py 文件，放进 plugins/）。新建后默认停用；"
        "插件里用 register(registry) 注册工具。给不出 code 时只写骨架。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "小写字母/数字/下划线，2-40 位"},
                "description": {"type": "string", "description": "这个插件是干什么的"},
                "code": {"type": "string", "description": "完整 Python 代码；省略则生成骨架"},
            },
            "required": ["name"],
        },
        plugin_create,
    )
    registry.register(
        "plugin_update",
        "改写已有插件的代码（整段覆盖）。已启用的插件会立刻热重载。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "code": {"type": "string", "description": "新的完整代码"},
            },
            "required": ["name", "code"],
        },
        plugin_update,
    )
    registry.register(
        "plugin_read",
        "看某个插件的源码，改之前先读。",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        plugin_read,
    )
    registry.register(
        "plugin_enable",
        "启用插件（执行它的代码并注册工具，立即生效）。必须用户明确同意后带 confirm=true。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "confirm": {"type": "boolean", "description": "用户已同意执行这个插件的代码"},
            },
            "required": ["name"],
        },
        plugin_enable,
    )
    registry.register(
        "plugin_disable",
        "停用插件并移除它注册的工具（文件保留，随时再开）。",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        plugin_disable,
    )
    registry.register(
        "plugin_reload",
        "重新扫描插件目录并重载所有已启用的插件（改完文件、或新塞了文件时用）。",
        {"type": "object", "properties": {}},
        plugin_reload,
    )
    registry.register(
        "plugin_delete",
        "删除插件文件并从注册表移除它的工具。要用户同意后带 confirm=true。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "confirm": {"type": "boolean", "description": "用户已同意删除"},
            },
            "required": ["name"],
        },
        plugin_delete,
    )
