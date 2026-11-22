"""Skill 工具：列出、阅读、载入到当前会话的系统提示词。"""

from __future__ import annotations

import json
from typing import Any

from ..errors import ToolError
from ..utils import truncate
from .base import ToolContext, ToolRegistry

MAX_SKILL_CHARS = 60_000


def register(registry: ToolRegistry, get_library) -> None:
    def list_skills(ctx: ToolContext, args: dict[str, Any]) -> str:
        library = get_library()
        library.refresh()
        skills = library.list()
        if not skills:
            return "当前没有任何 skill（把 SKILL.md 放在 skills/ 目录下即可被识别）"
        return json.dumps(skills, ensure_ascii=False, indent=2)

    def read_skill(ctx: ToolContext, args: dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip()
        if not name:
            raise ToolError("name 不能为空")
        return truncate(get_library().read(name), MAX_SKILL_CHARS, note="skill 内容过长已截断")

    def load_skill(ctx: ToolContext, args: dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip()
        if not name:
            raise ToolError("name 不能为空")
        library = get_library()
        info = library.get(name)
        if info.name in ctx.session.loaded_skills:
            return f"技能 {info.name} 已在当前会话中载入过，内容见系统提示词。"
        content = truncate(library.read(name), MAX_SKILL_CHARS, note="skill 内容过长已截断")
        block = (
            f"\n\n## 已载入技能：{info.name}\n"
            f"> 技能目录：{info.directory}\n"
            f"> 需要执行其中的脚本时，用该目录下的文件（注意该目录可能不在沙箱内，"
            f"不在沙箱内时请先复制进工作区再运行）。\n\n{content}"
        )
        if len(ctx.session.system_prompt or "") + len(block) > 200_000:
            raise ToolError("系统提示词过长，无法再载入该 skill")
        ctx.session.append_system_prompt(block, mode="append")
        ctx.session.loaded_skills.append(info.name)
        return f"OK: 已把 skill《{info.name}》载入当前会话（{len(content)} 字符），后续轮次一直生效。"

    registry.register(
        "list_skills",
        "列出当前可用的所有 skill（名称、描述、目录）。",
        {"type": "object", "properties": {}},
        list_skills,
    )
    registry.register(
        "read_skill",
        "读取某个 skill 的完整 SKILL.md 内容（不改变系统提示词）。",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        read_skill,
    )
    registry.register(
        "load_skill",
        "把某个 skill 的完整说明载入当前会话的系统提示词，之后每一轮都生效。适合‘按这个技能来做’。",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        load_skill,
    )
