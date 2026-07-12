import asyncio

from app.domain.models.event import ToolEvent, ToolEventStatus
from app.domain.models.tool_result import ToolResult
from app.domain.services.agent_task_runner import AgentTaskRunner


def test_called_skill_event_exposes_only_summary() -> None:
    async def scenario() -> None:
        runner = object.__new__(AgentTaskRunner)
        event = ToolEvent(
            tool_call_id="call-id",
            tool_name="skill",
            function_name="load_skill",
            function_args={"name": "demo"},
            function_result=ToolResult(
                success=True,
                data={
                    "name": "demo",
                    "skill_dir": "/skills/demo",
                    "content": "FULL BODY",
                },
            ),
            status=ToolEventStatus.CALLED,
        )

        await runner._handle_tool_event(event)

        assert event.tool_content.model_dump() == {
            "name": "demo",
            "skill_dir": "/skills/demo",
        }

    asyncio.run(scenario())
