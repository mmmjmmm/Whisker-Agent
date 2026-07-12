import asyncio

import pytest

from app.domain.models.skill import SkillSnapshot
from app.domain.models.tool_result import ToolResult
from app.domain.services.skills.runtime import (
    SkillLoadError,
    SkillNotFoundError,
    SkillRuntime,
)


class FakeSandbox:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.uploads: list[tuple[str, bytes]] = []
        self.commands: list[str] = []

    async def upload_file(self, file_data, filepath, filename=None):
        self.uploads.append((filepath, file_data.read()))
        return ToolResult(success=True)

    async def exec_command(self, session_id, exec_dir, command):
        self.commands.append(command)
        return ToolResult(
            success=True,
            data={
                "session_id": session_id,
                "status": "completed",
                "returncode": self.returncode,
            },
        )

    async def wait_process(self, session_id, seconds=None):
        return ToolResult(success=True, data={"returncode": self.returncode})


def make_snapshot(
    *,
    skill_id: str = "skill-id",
    name: str = "demo",
    description: str = "处理演示任务",
    root_path: str = "demo",
    bundle_bytes: bytes | None = b"zip-bytes",
    bundle_load_error: str | None = None,
) -> SkillSnapshot:
    return SkillSnapshot(
        id=skill_id,
        name=name,
        description=description,
        skill_md=f"FULL {name} SKILL BODY",
        root_path=root_path,
        bundle_bytes=bundle_bytes,
        bundle_load_error=bundle_load_error,
    )


def test_runtime_syncs_full_bundle_once_for_concurrent_loads() -> None:
    async def scenario() -> None:
        sandbox = FakeSandbox()
        runtime = SkillRuntime((make_snapshot(),), sandbox)

        first, second = await asyncio.gather(
            runtime.load("demo"),
            runtime.load("demo"),
        )

        expected_dir = (
            "/home/ubuntu/.mooc-manus/skills/skill-id/content/demo"
        )
        assert first.skill_dir == expected_dir
        assert second.skill_dir == expected_dir
        assert sandbox.uploads == [
            (
                "/home/ubuntu/.mooc-manus/skills/skill-id/bundle.zip",
                b"zip-bytes",
            )
        ]
        assert len(sandbox.commands) == 1
        assert "python3 -m zipfile -e" in sandbox.commands[0]

    asyncio.run(scenario())


def test_runtime_replaces_existing_content_before_extracting() -> None:
    async def scenario() -> None:
        sandbox = FakeSandbox()
        runtime = SkillRuntime((make_snapshot(),), sandbox)

        await runtime.load("demo")

        content_dir = "/home/ubuntu/.mooc-manus/skills/skill-id/content"
        assert sandbox.commands == [
            f"rm -rf {content_dir} && python3 -m zipfile -e "
            "/home/ubuntu/.mooc-manus/skills/skill-id/bundle.zip "
            f"{content_dir}"
        ]

    asyncio.run(scenario())


def test_catalog_contains_only_metadata() -> None:
    runtime = SkillRuntime(
        (
            make_snapshot(),
            make_snapshot(
                skill_id="second-id",
                name="second",
                description="另一个任务",
            ),
        ),
        FakeSandbox(),
    )

    assert runtime.names == ("demo", "second")
    assert runtime.has_skills is True
    assert "<name>demo</name>" in runtime.catalog_prompt
    assert "处理演示任务" in runtime.catalog_prompt
    assert "FULL demo SKILL BODY" not in runtime.catalog_prompt
    assert "load_skill" in runtime.catalog_prompt


def test_runtime_reports_missing_and_unavailable_skills() -> None:
    async def scenario() -> None:
        runtime = SkillRuntime(
            (
                make_snapshot(
                    bundle_bytes=None,
                    bundle_load_error="OSS unavailable",
                ),
            ),
            FakeSandbox(),
        )

        with pytest.raises(SkillNotFoundError, match="missing"):
            await runtime.load("missing")
        with pytest.raises(SkillLoadError, match="OSS unavailable"):
            await runtime.load("demo")

    asyncio.run(scenario())


def test_runtime_reports_extract_failure() -> None:
    async def scenario() -> None:
        runtime = SkillRuntime((make_snapshot(),), FakeSandbox(returncode=1))

        with pytest.raises(SkillLoadError, match="解压失败"):
            await runtime.load("demo")

    asyncio.run(scenario())


def test_empty_runtime_has_no_catalog() -> None:
    runtime = SkillRuntime((), FakeSandbox())

    assert runtime.names == ()
    assert runtime.has_skills is False
    assert runtime.catalog_prompt == ""
