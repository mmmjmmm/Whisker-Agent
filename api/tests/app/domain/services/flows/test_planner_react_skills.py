from app.domain.models.app_config import AgentConfig
from app.domain.models.skill import SkillSnapshot
from app.domain.services.flows.planner_react import PlannerReActFlow
from app.domain.services.skills.runtime import SkillRuntime
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.mcp import MCPTool


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


def function_names(agent) -> set[str]:
    return {
        schema["function"]["name"]
        for schema in agent._get_available_tools()
    }


def build_flow(snapshots=()) -> PlannerReActFlow:
    sandbox = object()
    runtime = SkillRuntime(snapshots, sandbox)
    return PlannerReActFlow(
        uow_factory=FakeUnitOfWork,
        llm=object(),
        agent_config=AgentConfig(),
        session_id="session-id",
        json_parser=object(),
        browser=object(),
        sandbox=sandbox,
        search_engine=object(),
        mcp_tool=MCPTool(),
        a2a_tool=A2ATool(),
        skill_runtime=runtime,
    )


def test_planner_and_react_receive_separate_skill_tools() -> None:
    snapshot = SkillSnapshot(
        id="skill-id",
        name="demo",
        description="demo description",
        skill_md="FULL BODY",
        root_path="demo",
        bundle_bytes=b"zip",
    )

    flow = build_flow((snapshot,))

    assert function_names(flow.planner) == {"load_skill"}
    assert "load_skill" in function_names(flow.react)
    assert "read_file" in function_names(flow.react)
    assert flow.planner._tools[-1] is not flow.react._tools[-1]
    assert "demo description" in flow.planner._system_prompt
    assert "FULL BODY" not in flow.planner._system_prompt


def test_no_skills_keeps_planner_tool_choice_disabled() -> None:
    flow = build_flow()

    assert function_names(flow.planner) == set()
    assert flow.planner._tool_choice == "none"
    assert "load_skill" not in function_names(flow.react)
