"""Skill 库：扫描目录下的 SKILL.md，支持列表、读取、加载进系统提示词。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ValidationError

logger = logging.getLogger("agent.skills")

DOC_NAMES = ("SKILL.md", "skill.md", "README.md", "readme.md")
_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MAX_SKILL_CHARS = 60000


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip("'\"")
    return meta, text[match.end():]


@dataclass
class Skill:
    name: str
    path: Path
    title: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "path": str(self.path),
        }


class SkillLibrary:
    def __init__(self, dirs: list[Path]):
        self.dirs = [Path(d) for d in dirs]

    def __len__(self) -> int:
        return len(self.list())

    def list(self) -> list[Skill]:
        found: dict[str, Skill] = {}
        for base in self.dirs:
            if not base.is_dir():
                continue
            for skill_file in sorted(base.glob("*/SKILL.md")):
                skill = self._load_meta(skill_file)
                found.setdefault(skill.name, skill)
        return sorted(found.values(), key=lambda s: s.name)

    def _load_meta(self, skill_file: Path) -> Skill:
        folder = skill_file.parent.name
        name, title, description = folder, folder, ""
        try:
            text = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("读取 Skill 失败 %s: %s", skill_file, exc)
            return Skill(name=folder, path=skill_file, title=folder, description="（读取失败）")
        meta, body = _parse_front_matter(text)
        name = meta.get("name") or folder
        description = meta.get("description") or ""
        if not description:
            for line in body.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    description = stripped[:200]
                    break
        for line in body.splitlines():
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                break
        return Skill(name=name, path=skill_file, title=title or name, description=description)

    def get(self, name: str) -> Skill:
        target = str(name or "").strip()
        if not target:
            raise ValidationError("skill 名称不能为空")
        for skill in self.list():
            if skill.name == target or skill.path.parent.name == target:
                return skill
        raise NotFoundError(f"Skill 不存在：{target}（可用：{[s.name for s in self.list()]}")

    def read(self, name: str) -> dict[str, Any]:
        skill = self.get(name)
        text = skill.path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > _MAX_SKILL_CHARS
        if truncated:
            text = text[:_MAX_SKILL_CHARS] + "\n\n...（超出长度被截断，需要完整内容请继续读取文件）"
        return {**skill.to_dict(), "content": text, "truncated": truncated}
