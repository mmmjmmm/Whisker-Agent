import asyncio

import pytest

from app.domain.services.skills.runtime import (
    LoadedSkill,
    SkillLoadError,
    SkillNotFoundError,
)
from app.domain.services.tools.skill import SkillTool


class FakeSkillRuntime:
    def __init__(self, skills: dict[str, LoadedSkill]) -> None:
        self.skills = skills
        self.calls: list[str] = []
        self.failure: Exception | None = None

    async def load(self, name: str) -> LoadedSkill:
        self.calls.append(name)
        if self.failure:
            raise self.failure
        if name not in self.skills:
            raise SkillNotFoundError(f"missing: {name}")
        return self.skills[name]


def loaded(name: str) -> LoadedSkill:
    return LoadedSkill(
        name=name,
        skill_md=f"FULL {name} BODY",
        skill_dir=f"/skills/{name}",
    )


def test_each_agent_tool_injects_full_skill_once() -> None:
    async def scenario() -> None:
        runtime = FakeSkillRuntime({"demo": loaded("demo")})
        first_agent_tool = SkillTool(runtime)
        second_agent_tool = SkillTool(runtime)

        first = await first_agent_tool.invoke("load_skill", name="demo")
        repeated = await first_agent_tool.invoke("load_skill", name="demo")
        second = await second_agent_tool.invoke("load_skill", name="demo")

        assert "FULL demo BODY" in first.data["content"]
        assert repeated.data == {
            "name": "demo",
            "skill_dir": "/skills/demo",
            "content": None,
            "already_loaded": True,
        }
        assert "FULL demo BODY" in second.data["content"]
        assert runtime.calls == ["demo", "demo"]

    asyncio.run(scenario())


def test_one_agent_can_load_multiple_skills() -> None:
    async def scenario() -> None:
        runtime = FakeSkillRuntime({
            "first": loaded("first"),
            "second": loaded("second"),
        })
        tool = SkillTool(runtime)

        first = await tool.invoke("load_skill", name="first")
        second = await tool.invoke("load_skill", name="second")

        assert "FULL first BODY" in first.data["content"]
        assert "FULL second BODY" in second.data["content"]
        assert runtime.calls == ["first", "second"]

    asyncio.run(scenario())


def test_missing_skill_returns_failed_tool_result() -> None:
    async def scenario() -> None:
        result = await SkillTool(FakeSkillRuntime({})).invoke(
            "load_skill",
            name="missing",
        )
        assert result.success is False
        assert result.message == "missing: missing"

    asyncio.run(scenario())


def test_runtime_failure_is_left_for_agent_retry() -> None:
    async def scenario() -> None:
        runtime = FakeSkillRuntime({"demo": loaded("demo")})
        runtime.failure = SkillLoadError("sync failed")

        with pytest.raises(SkillLoadError, match="sync failed"):
            await SkillTool(runtime).invoke("load_skill", name="demo")

    asyncio.run(scenario())
