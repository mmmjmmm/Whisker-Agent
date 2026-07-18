import asyncio
from dataclasses import dataclass, field

import pytest

from app.domain.models.event import DoneEvent, MessageEvent, WaitEvent
from app.domain.models.message import Message
from app.domain.models.team import AgentMode
from app.domain.models.trace import TraceSpan, TraceSpanType
from app.domain.models.trace import TraceSpanStatus
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.services.tracing.recorder import TraceRecorder


@dataclass
class FakeTraceRepository:
    spans: dict[str, TraceSpan] = field(default_factory=dict)

    async def create_span(self, span: TraceSpan) -> None:
        self.spans[span.id] = span

    async def finish_span(self, span: TraceSpan) -> None:
        self.spans[span.id] = span

    async def list_by_session(self, session_id: str) -> list[TraceSpan]:
        return [
            span for span in self.spans.values()
            if span.session_id == session_id
        ]

    async def list_by_trace(
        self,
        session_id: str,
        trace_id: str,
    ) -> list[TraceSpan]:
        return [
            span
            for span in self.spans.values()
            if span.session_id == session_id and span.trace_id == trace_id
        ]


class FakeUow:
    def __init__(self, repo: FakeTraceRepository) -> None:
        self.trace = repo

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


def test_trace_recorder_records_nested_llm_and_tool_spans() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")

        root = await recorder.start_span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        )
        flow = await recorder.start_span(
            span_type=TraceSpanType.FLOW,
            name="planner_react",
        )
        llm = await recorder.start_span(
            span_type=TraceSpanType.LLM,
            name="deepseek-chat",
            attributes={"model": "deepseek-chat", "total_tokens": 12},
        )
        await recorder.end_span(llm, output={"content": "tool call"})
        tool = await recorder.start_span(
            span_type=TraceSpanType.TOOL,
            name="shell_exec",
            attributes={"function_name": "shell_exec", "success": True},
        )
        await recorder.end_span(tool, output={"success": True})
        await recorder.end_span(flow)
        await recorder.end_span(root)

        spans = list(repo.spans.values())
        assert {span.span_type for span in spans} == {
            TraceSpanType.ROOT,
            TraceSpanType.FLOW,
            TraceSpanType.LLM,
            TraceSpanType.TOOL,
        }
        assert (
            next(
                span for span in spans
                if span.span_type is TraceSpanType.FLOW
            ).parent_span_id
            == root.id
        )
        assert (
            next(
                span for span in spans
                if span.span_type is TraceSpanType.LLM
            ).parent_span_id
            == flow.id
        )

    asyncio.run(scenario())


class FakeSessionRepository:
    def __init__(self) -> None:
        self.events = []
        self.statuses = []

    async def add_event(self, session_id, event) -> None:
        self.events.append(event)

    async def update_status(self, session_id, status) -> None:
        self.statuses.append(status)


class RunnerUnitOfWork(FakeUow):
    def __init__(
        self,
        repo: FakeTraceRepository,
        sessions: FakeSessionRepository,
    ) -> None:
        super().__init__(repo)
        self.session = sessions


class FakeInputStream:
    def __init__(self, events) -> None:
        self.items = [
            (f"input-{index}", event.model_dump_json())
            for index, event in enumerate(events, start=1)
        ]

    async def pop(self):
        return self.items.pop(0)

    async def is_empty(self) -> bool:
        return not self.items


class FakeOutputStream:
    def __init__(self, input_stream: FakeInputStream | None = None) -> None:
        self._input_stream = input_stream
        self.items = []
        self._injected = False

    async def put(self, value: str) -> str:
        self.items.append(value)
        if self._input_stream is not None and not self._injected:
            self._injected = True
            event = MessageEvent(
                role="user",
                message="second",
                agent_mode=AgentMode.REACT,
            )
            self._input_stream.items.append(("input-2", event.model_dump_json()))
        return f"output-{len(self.items)}"


class FakeTask:
    def __init__(self, input_stream, output_stream) -> None:
        self.input_stream = input_stream
        self.output_stream = output_stream


class YieldingFlow:
    done = True

    async def invoke(self, message):
        yield DoneEvent()


class WaitingFlow:
    done = False

    async def invoke(self, message):
        yield WaitEvent()


class BlockingFlow:
    done = False

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def invoke(self, message):
        self.started.set()
        await asyncio.Future()
        yield DoneEvent()


class FakeSandbox:
    async def ensure_sandbox(self) -> None:
        return None


class FakeMCPTool:
    async def initialize(self, config) -> None:
        return None

    async def cleanup(self) -> None:
        return None


class FakeA2AManager:
    async def cleanup(self) -> None:
        return None


class FakeA2ATool:
    def __init__(self) -> None:
        self.manager = FakeA2AManager()

    async def initialize(self, config) -> None:
        return None


def make_runner(repository, sessions, flow):
    runner = object.__new__(AgentTaskRunner)
    runner._session_id = "session-1"
    runner._uow_factory = lambda: RunnerUnitOfWork(repository, sessions)
    runner._uow = RunnerUnitOfWork(repository, sessions)
    runner._trace_recorder = TraceRecorder(
        runner._uow_factory,
        session_id="session-1",
    )
    runner._react_flow = flow
    runner._team_flow_factory = lambda: flow
    runner._active_flow = None
    runner._sandbox = FakeSandbox()
    runner._mcp_tool = FakeMCPTool()
    runner._mcp_config = object()
    runner._a2a_tool = FakeA2ATool()
    runner._a2a_config = object()
    return runner


def test_put_and_add_event_does_not_create_event_span() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        sessions = FakeSessionRepository()
        runner = make_runner(repository, sessions, YieldingFlow())
        task = FakeTask(FakeInputStream([]), FakeOutputStream())

        async with runner._trace_recorder.span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        ):
            await runner._put_and_add_event(task, DoneEvent())

        assert sessions.events
        assert all(
            span.span_type is not TraceSpanType.EVENT
            for span in repository.spans.values()
        )

    asyncio.run(scenario())


def test_run_flow_marks_waiting_when_wait_event_closes_consumer() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        sessions = FakeSessionRepository()
        runner = make_runner(repository, sessions, WaitingFlow())

        async with runner._trace_recorder.span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        ):
            iterator = runner._run_flow(Message(message="wait"))
            assert isinstance(await anext(iterator), WaitEvent)
            await iterator.aclose()

        flow_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.FLOW
        )
        assert flow_span.status is TraceSpanStatus.WAITING

    asyncio.run(scenario())


def test_run_flow_marks_asyncio_cancellation() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        sessions = FakeSessionRepository()
        flow = BlockingFlow()
        runner = make_runner(repository, sessions, flow)

        async def consume() -> None:
            async with runner._trace_recorder.span(
                span_type=TraceSpanType.ROOT,
                name="chat",
                trace_id="trace-1",
            ):
                await anext(runner._run_flow(Message(message="cancel")))

        task = asyncio.create_task(consume())
        await flow.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        spans = list(repository.spans.values())
        assert {
            span.span_type: span.status
            for span in spans
        } == {
            TraceSpanType.ROOT: TraceSpanStatus.CANCELLED,
            TraceSpanType.FLOW: TraceSpanStatus.CANCELLED,
        }

    asyncio.run(scenario())


def test_new_input_cancels_old_root_and_flow_with_reason() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        sessions = FakeSessionRepository()
        runner = make_runner(repository, sessions, YieldingFlow())
        input_stream = FakeInputStream([
            MessageEvent(
                role="user",
                message="first",
                agent_mode=AgentMode.REACT,
            )
        ])
        task = FakeTask(
            input_stream,
            FakeOutputStream(input_stream),
        )

        await runner.invoke(task)

        roots = sorted(
            (
                span for span in repository.spans.values()
                if span.span_type is TraceSpanType.ROOT
            ),
            key=lambda span: span.started_at,
        )
        flows = sorted(
            (
                span for span in repository.spans.values()
                if span.span_type is TraceSpanType.FLOW
            ),
            key=lambda span: span.started_at,
        )
        assert len(roots) == 2
        assert len(flows) == 2
        assert roots[0].status is TraceSpanStatus.CANCELLED
        assert flows[0].status is TraceSpanStatus.CANCELLED
        assert roots[0].attributes["cancellation_reason"] == (
            "superseded_by_new_input"
        )
        assert flows[0].attributes["cancellation_reason"] == (
            "superseded_by_new_input"
        )
        assert roots[1].status is TraceSpanStatus.OK
        assert flows[1].status is TraceSpanStatus.OK

    asyncio.run(scenario())
