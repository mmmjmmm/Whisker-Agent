import asyncio
from dataclasses import dataclass, field

from app.domain.models.event import (
    MessageEvent,
    PlanEvent,
    PlanEventStatus,
    StepEvent,
    StepEventStatus,
    WaitEvent,
)
from app.domain.models.message import Message
from app.domain.models.plan import ExecutionStatus, Plan, Step
from app.domain.models.session import SessionStatus
from app.domain.models.trace import TraceSpan, TraceSpanStatus, TraceSpanType
from app.domain.services.flows.base import FlowStatus
from app.domain.services.flows.planner_react import PlannerReActFlow
from app.domain.services.tracing import TraceRecorder


@dataclass
class FakeTraceRepository:
    spans: dict[str, TraceSpan] = field(default_factory=dict)

    async def create_span(self, span: TraceSpan) -> None:
        self.spans[span.id] = span

    async def finish_span(self, span: TraceSpan) -> None:
        self.spans[span.id] = span


class FakeSession:
    status = SessionStatus.PENDING

    def get_latest_plan(self):
        return None


class FakeSessionRepository:
    def __init__(self) -> None:
        self.session = FakeSession()

    async def get_by_id(self, session_id: str):
        return self.session

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        self.session.status = status


class FakeUnitOfWork:
    def __init__(
        self,
        repository: FakeTraceRepository,
        sessions: FakeSessionRepository,
    ) -> None:
        self.trace = repository
        self.session = sessions

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FakePlanner:
    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    async def roll_back(self, message) -> None:
        return None

    async def create_plan(self, message):
        yield PlanEvent(plan=self._plan, status=PlanEventStatus.CREATED)

    async def update_plan(self, plan, step):
        yield PlanEvent(plan=plan, status=PlanEventStatus.UPDATED)


class FakeReact:
    name = "react"

    def __init__(self, recorder: TraceRecorder, *, wait: bool) -> None:
        self._recorder = recorder
        self._wait = wait
        self.summary_attachments = None

    async def roll_back(self, message) -> None:
        return None

    async def execute_step(self, plan, step, message):
        async with self._recorder.span(
            span_type=TraceSpanType.AGENT,
            name="react.execute_step",
            attributes={"step_id": step.id},
        ) as scope:
            step.status = ExecutionStatus.RUNNING
            yield StepEvent(step=step, status=StepEventStatus.STARTED)
            if self._wait:
                scope.finish(
                    status=TraceSpanStatus.WAITING,
                    output={"step_id": step.id, "status": "waiting"},
                )
                yield WaitEvent()
                return
            step.status = ExecutionStatus.COMPLETED
            step.success = True
            step.result = "done"
            step.attachments = [
                "/home/ubuntu/report.md",
                "/home/ubuntu/report.md",
            ]
            scope.finish(output=step.model_dump(mode="json"))
            yield StepEvent(step=step, status=StepEventStatus.COMPLETED)

    async def compact_memory(self) -> None:
        return None

    async def summarize(self):
        yield MessageEvent(role="assistant", message="final")

    async def summarize_stream(self, attachments=None):
        self.summary_attachments = attachments
        yield MessageEvent(role="assistant", message="final")


def build_flow(*, wait: bool):
    repository = FakeTraceRepository()
    sessions = FakeSessionRepository()
    recorder = TraceRecorder(
        lambda: FakeUnitOfWork(repository, sessions),
        session_id="session-1",
    )
    plan = Plan(
        id="plan-1",
        title="Trace",
        goal="trace this",
        language="zh",
        steps=[Step(id="step-1", description="execute")],
        message="planned",
    )
    flow = object.__new__(PlannerReActFlow)
    flow._uow = FakeUnitOfWork(repository, sessions)
    flow._session_id = "session-1"
    flow._trace_recorder = recorder
    flow.status = FlowStatus.IDLE
    flow.plan = None
    flow.planner = FakePlanner(plan)
    flow.react = FakeReact(recorder, wait=wait)
    return flow, recorder, repository


def test_planner_react_flow_records_step_task_hierarchy() -> None:
    async def scenario() -> None:
        flow, recorder, repository = build_flow(wait=False)

        async with recorder.span(
            span_type=TraceSpanType.FLOW,
            name="planner_react",
            trace_id="trace-1",
        ) as flow_scope:
            _ = [
                event
                async for event in flow.invoke(Message(message="trace this"))
            ]

        assert flow.react.summary_attachments == ["/home/ubuntu/report.md"]
        task_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.TASK
        )
        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert task_span.name == "plan.step"
        assert task_span.parent_span_id == flow_scope.handle.id
        assert task_span.status is TraceSpanStatus.OK
        assert task_span.attributes["plan_id"] == "plan-1"
        assert task_span.attributes["step_id"] == "step-1"
        assert task_span.attributes["description"] == "execute"
        assert task_span.output["status"] == "completed"
        assert agent_span.parent_span_id == task_span.id

    asyncio.run(scenario())


def test_planner_react_flow_preserves_waiting_step_task() -> None:
    async def scenario() -> None:
        flow, recorder, repository = build_flow(wait=True)

        async with recorder.span(
            span_type=TraceSpanType.FLOW,
            name="planner_react",
            trace_id="trace-1",
        ):
            iterator = flow.invoke(Message(message="trace this"))
            while True:
                event = await anext(iterator)
                if isinstance(event, WaitEvent):
                    break
            await iterator.aclose()

        task_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.TASK
        )
        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert task_span.status is TraceSpanStatus.WAITING
        assert task_span.output == {
            "step_id": "step-1",
            "status": "waiting",
        }
        assert agent_span.status is TraceSpanStatus.WAITING
        assert agent_span.parent_span_id == task_span.id

    asyncio.run(scenario())
