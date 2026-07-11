from pydantic import TypeAdapter

from app.domain.models.event import (
    Event,
    MessageEvent,
    TaskGraphEvent,
    TeamTaskEvent,
    ToolEvent,
    ToolEventStatus,
)
from app.domain.models.team import (
    AgentMode,
    PlannedTask,
    PlannedTaskGraph,
    TeamCapability,
)
from app.domain.services.team.graph import build_task_graph
from app.interfaces.schemas.event import EventMapper


def graph():
    plan = PlannedTaskGraph(
        title="t",
        goal="g",
        tasks=[
            PlannedTask(
                id="a",
                description="a",
                capability=TeamCapability.SEARCH,
                success_criteria="done",
            )
        ],
    )
    return build_task_graph(plan, 5)


def test_team_events_round_trip_and_map_to_sse():
    task_graph = graph()
    events = [
        TaskGraphEvent(graph=task_graph),
        TeamTaskEvent(
            graph_id=task_graph.id,
            task=task_graph.tasks[0],
            agent_id="worker-1",
            attempt=1,
        ),
    ]

    for event in events:
        parsed = TypeAdapter(Event).validate_json(event.model_dump_json())
        sse = EventMapper.event_to_sse_event(parsed)
        assert parsed.type == event.type
        assert sse.event == event.type
        assert sse.data.event_id == event.id


def test_tool_event_keeps_team_metadata():
    event = ToolEvent(
        tool_call_id="call-1",
        tool_name="search",
        function_name="search_web",
        function_args={"query": "x"},
        status=ToolEventStatus.CALLING,
        graph_id="g",
        task_id="a",
        agent_id="worker-1",
        attempt=1,
    )

    data = EventMapper.event_to_sse_event(event).data

    assert data.graph_id == "g"
    assert data.task_id == "a"
    assert data.agent_id == "worker-1"
    assert data.attempt == 1


def test_user_message_mode_survives_domain_and_sse_mapping():
    event = MessageEvent(role="user", message="research", agent_mode=AgentMode.TEAM)

    parsed = TypeAdapter(Event).validate_json(event.model_dump_json())
    data = EventMapper.event_to_sse_event(parsed).data

    assert parsed.agent_mode is AgentMode.TEAM
    assert data.agent_mode is AgentMode.TEAM
