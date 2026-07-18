import asyncio

from app.domain.models.event import ErrorEvent, MessageEvent
from app.domain.models.message import Message
from app.domain.models.team import (
    FinalTeamResponse,
    TaskGraph,
    TaskGraphStatus,
    TeamCapability,
    TeamTask,
    TeamTaskStatus,
    WorkerResult,
)
from app.domain.services.flows.team import TeamFlow
from app.domain.services.team.graph import TaskGraphError


def make_graph() -> TaskGraph:
    return TaskGraph(
        id="graph-1",
        title="Trace",
        goal="trace this",
        tasks=[
            TeamTask(
                id="task-1",
                description="analyze",
                capability=TeamCapability.ANALYSIS,
                success_criteria="done",
            )
        ],
    )


class RetryPlanner:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str | None]] = []

    async def create_graph(
        self,
        message,
        validation_error,
        *,
        attempt,
        max_attempts,
    ) -> TaskGraph:
        self.calls.append((attempt, max_attempts, validation_error))
        if attempt == 1:
            raise TaskGraphError("invalid dependency")
        return make_graph()

    def drain_skill_events(self):
        return []


class CompletingOrchestrator:
    async def run(self, graph, attachments, emit):
        graph.tasks[0].status = TeamTaskStatus.COMPLETED
        graph.tasks[0].result = WorkerResult(success=True, summary="done")
        graph.status = TaskGraphStatus.COMPLETED
        return graph


class RetrySynthesizer:
    def __init__(self, calls: list[tuple[int, int]]) -> None:
        self._calls = calls

    async def synthesize(self, graph, *, attempt, max_attempts):
        self._calls.append((attempt, max_attempts))
        if attempt == 1:
            raise RuntimeError("invalid summary")
        return FinalTeamResponse(message="final answer")

    def drain_skill_events(self):
        return []


def test_team_flow_passes_attempt_context_to_planner_and_synthesizer() -> None:
    async def scenario() -> None:
        planner = RetryPlanner()
        synth_calls: list[tuple[int, int]] = []
        flow = TeamFlow(
            team_max_tasks=5,
            planner=planner,
            orchestrator=CompletingOrchestrator(),
            synthesizer_factory=lambda: RetrySynthesizer(synth_calls),
        )

        events = [event async for event in flow.invoke(Message(message="trace this"))]

        assert not any(isinstance(event, ErrorEvent) for event in events)
        assert any(
            isinstance(event, MessageEvent) and event.message == "final answer"
            for event in events
        )
        assert planner.calls == [
            (1, 2, None),
            (2, 2, "invalid dependency"),
        ]
        assert synth_calls == [(1, 2), (2, 2)]

    asyncio.run(scenario())
