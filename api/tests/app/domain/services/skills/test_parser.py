from io import BytesIO
from zipfile import ZipFile

import pytest

from app.domain.services.skills.parser import SkillParseError, SkillParser


def build_zip(entries: list[tuple[str, str]]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for path, content in entries:
            archive.writestr(path, content)
    return stream.getvalue()


def test_parse_nested_skill_bundle() -> None:
    bundle = build_zip([
        (
            "demo/SKILL.md",
            "---\nname: demo-skill\ndescription: 处理演示任务\n---\n"
            "# Demo\n按步骤执行。",
        ),
        ("demo/references/guide.md", "# Guide"),
        ("demo/scripts/run.py", "print('ok')"),
    ])

    parsed = SkillParser().parse(bundle)

    assert parsed.name == "demo-skill"
    assert parsed.description == "处理演示任务"
    assert parsed.root_path == "demo"
    assert parsed.skill_md.endswith("# Demo\n按步骤执行。")


def test_first_skill_md_wins() -> None:
    bundle = build_zip([
        (
            "first/SKILL.md",
            "---\nname: first\ndescription: first skill\n---\n",
        ),
        (
            "second/SKILL.md",
            "---\nname: second\ndescription: second skill\n---\n",
        ),
    ])

    assert SkillParser().parse(bundle).name == "first"


@pytest.mark.parametrize(
    "entries",
    [
        [("README.md", "no skill")],
        [("SKILL.md", "# no frontmatter")],
        [("SKILL.md", "---\ndescription: missing name\n---\n")],
        [("SKILL.md", "---\nname: demo\n---\n")],
    ],
)
def test_parse_rejects_missing_runtime_fields(
    entries: list[tuple[str, str]],
) -> None:
    with pytest.raises(SkillParseError):
        SkillParser().parse(build_zip(entries))
