import asyncio
import copy
import json

from app.domain.models.app_config import AgentConfig
from app.domain.models.memory import Memory
from app.domain.models.message import Message
from app.domain.models.skill import SkillSnapshot
from app.domain.models.team import TeamCapability, TeamTask
from app.domain.services.agents.team_planner import TeamPlannerAgent
from app.domain.services.flows.team import build_team_flow
from app.domain.services.skills.runtime import LoadedSkill, SkillRuntime
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.skill import SkillTool


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FakeJSONParser:
    async def invoke(self, text: str, default_value=None):
        return json.loads(text)


class FakeRuntime:
    async def load(self, name: str) -> LoadedSkill:
        return LoadedSkill(name=name, skill_md="FULL BODY", skill_dir="/skills/demo")


class PlannerLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, messages, tools=None, response_format=None, tool_choice=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-id",
                        "function": {
                            "name": "load_skill",
                            "arguments": json.dumps({"name": "demo"}),
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": json.dumps({
                "title": "title",
                "goal": "goal",
                "tasks": [
                    {
                        "id": "task-1",
                        "description": "analyze",
                        "dependencies": [],
                        "capability": "analysis",
                        "success_criteria": "done",
                    }
                ],
            }),
        }


def names(agent) -> set[str]:
    return {
        schema["function"]["name"]
        for schema in agent._get_available_tools()
    }


def task(capability: TeamCapability) -> TeamTask:
    return TeamTask(
        id="task-1",
        description="work",
        dependencies=[],
        capability=capability,
        success_criteria="done",
    )


def test_team_agents_get_skill_without_expanding_worker_capability() -> None:
    snapshot = SkillSnapshot(
        id="skill-id",
        name="demo",
        description="demo description",
        skill_md="FULL BODY",
        root_path="demo",
        bundle_bytes=b"zip",
    )
    runtime = SkillRuntime((snapshot,), object())
    flow = build_team_flow(
        uow_factory=FakeUnitOfWork,
        session_id="session-id",
        agent_config=AgentConfig(),
        llm=object(),
        json_parser=object(),
        browser=object(),
        sandbox=object(),
        search_engine=object(),
        mcp_tool=MCPTool(),
        a2a_tool=A2ATool(),
        skill_runtime=runtime,
    )

    assert names(flow._planner) == {"load_skill"}
    assert names(flow._synthesizer_factory()) == {"load_skill"}

    analysis_worker = flow._orchestrator._worker_factory(
        "graph", "worker", task(TeamCapability.ANALYSIS), 1
    )
    shell_worker = flow._orchestrator._worker_factory(
        "graph", "worker", task(TeamCapability.SHELL), 1
    )
    file_worker = flow._orchestrator._worker_factory(
        "graph", "worker", task(TeamCapability.FILE_READ), 1
    )

    assert names(analysis_worker) == {"load_skill"}
    assert "load_skill" in names(shell_worker)
    assert "shell_execute" in names(shell_worker)
    assert names(file_worker) == {
        "load_skill",
        "read_file",
        "search_in_file",
        "find_files",
    }
    assert "shell_execute" not in names(file_worker)
    assert "demo description" in analysis_worker._system_prompt
    assert "FULL BODY" not in analysis_worker._system_prompt


def test_team_planner_forwards_skill_tool_events() -> None:
    async def scenario() -> None:
        planner = TeamPlannerAgent(
            uow_factory=FakeUnitOfWork,
            session_id="session-id",
            agent_config=AgentConfig(),
            llm=PlannerLLM(),
            json_parser=FakeJSONParser(),
            tools=[SkillTool(FakeRuntime())],
            memory=Memory(),
            system_prompt_suffix="catalog",
        )
        events = []

        async def emit(event) -> None:
            events.append(copy.deepcopy(event))

        graph = await planner.create_graph(Message(message="goal"), emit=emit)

        assert graph.goal == "goal"
        assert [event.status.value for event in events] == ["calling", "called"]
        assert all(event.tool_name == "skill" for event in events)

    asyncio.run(scenario())
