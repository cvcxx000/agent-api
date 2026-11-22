"""Skill 加载：扫描 SKILL.md，支持列表 / 阅读 / 载入到会话系统提示词。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .errors import NotFoundError

logger = logging.getLogger("agent.skills")


@dataclass
class SkillInfo:
    name: str
    description: str
    path: str
    directory: str
    root: str

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "path": self.path, "directory": self.directory}


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """只解析简单的 `key: value` 形式 frontmatter，不引入 YAML 依赖。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in header.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        meta[key.strip()] = value
    return meta, body


def _first_paragraph(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:200]
    return ""


class SkillLibrary:
    def __init__(self, roots: tuple[Path, ...]):
        self.roots = tuple(Path(r) for r in roots)
        self._skills: dict[str, SkillInfo] = {}
        self.refresh()

    def refresh(self) -> None:
        found: dict[str, SkillInfo] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for path in root.glob("**/SKILL.md"):
                try:
                    if len(path.relative_to(root).parts) > 4:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except (OSError, ValueError):
                    continue
                meta, body = _parse_frontmatter(text)
                name = str(meta.get("name") or path.parent.name).strip()
                if not name or name in found:
                    continue
                description = str(meta.get("description") or "").strip() or _first_paragraph(body)
                found[name] = SkillInfo(
                    name=name,
                    description=description[:300],
                    path=str(path),
                    directory=str(path.parent),
                    root=str(root),
                )
        self._skills = found

    def list(self) -> list[dict]:
        return [s.to_dict() for s in sorted(self._skills.values(), key=lambda x: x.name)]

    def get(self, name: str) -> SkillInfo:
        normalized = name.strip()
        if normalized in self._skills:
            return self._skills[normalized]
        lowered = normalized.lower()
        for key, info in self._skills.items():
            if key.lower() == lowered or Path(info.directory).name.lower() == lowered:
                return info
        raise NotFoundError(f"未找到 skill：{name}，可用：{', '.join(sorted(self._skills)) or '（无）'}")

    def read(self, name: str) -> str:
        return Path(self.get(name).path).read_text(encoding="utf-8", errors="replace")
