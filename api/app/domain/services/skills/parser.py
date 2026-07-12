import re
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

import yaml

from app.domain.models.skill import ParsedSkill


class SkillParseError(ValueError):
    """Skill ZIP 缺少运行必需信息。"""


class SkillParser:
    """从 Skill ZIP 的首个 SKILL.md 提取元数据。"""

    _frontmatter = re.compile(
        r"\A---[ \t]*\r?\n(?P<yaml>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|\Z)",
        re.DOTALL,
    )

    def parse(self, bundle: bytes) -> ParsedSkill:
        try:
            with ZipFile(BytesIO(bundle)) as archive:
                skill_path = next(
                    (
                        name
                        for name in archive.namelist()
                        if not name.endswith("/")
                        and PurePosixPath(name).name == "SKILL.md"
                    ),
                    None,
                )
                if skill_path is None:
                    raise SkillParseError("ZIP 中未找到 SKILL.md")
                skill_md = archive.read(skill_path).decode("utf-8")
        except (BadZipFile, UnicodeDecodeError) as exc:
            raise SkillParseError("无法读取 Skill ZIP") from exc

        match = self._frontmatter.match(skill_md)
        if match is None:
            raise SkillParseError("SKILL.md 缺少 YAML frontmatter")

        try:
            metadata = yaml.safe_load(match.group("yaml")) or {}
        except yaml.YAMLError as exc:
            raise SkillParseError("SKILL.md YAML frontmatter 无法解析") from exc
        if not isinstance(metadata, dict):
            raise SkillParseError("SKILL.md YAML frontmatter 必须是对象")
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            raise SkillParseError("SKILL.md 缺少 name")
        if not isinstance(description, str) or not description.strip():
            raise SkillParseError("SKILL.md 缺少 description")

        parent = PurePosixPath(skill_path).parent.as_posix()
        return ParsedSkill(
            name=name.strip(),
            description=description.strip(),
            skill_md=skill_md,
            root_path="" if parent == "." else parent,
        )
