import asyncio
from dataclasses import dataclass, field

import pytest

from app.domain.models.trace import TraceSpan, TraceSpanStatus, TraceSpanType
from app.domain.services.tracing.recorder import TraceRecorder, redact_and_truncate


@dataclass
class FakeTraceRepository:
    spans: dict[str, TraceSpan] = field(default_factory=dict)
    fail_writes: bool = False

    async def create_span(self, span: TraceSpan) -> None:
        if self.fail_writes:
            raise RuntimeError("write failed")
        self.spans[span.id] = span

    async def finish_span(self, span: TraceSpan) -> None:
        if self.fail_writes:
            raise RuntimeError("write failed")
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


def test_redact_and_truncate_redacts_sensitive_keys() -> None:
    result = redact_and_truncate(
        {
            "api_key": "secret-value",
            "nested": {
                "Authorization": "Bearer token",
                "safe": "value",
            },
        },
        max_bytes=1024,
    )

    assert result["api_key"] == "***"
    assert result["nested"]["Authorization"] == "***"
    assert result["nested"]["safe"] == "value"


def test_redact_and_truncate_keeps_token_usage_metrics() -> None:
    result = redact_and_truncate(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "api_token": "secret-value",
        },
        max_bytes=1024,
    )

    assert result["prompt_tokens"] == 10
    assert result["completion_tokens"] == 5
    assert result["total_tokens"] == 15
    assert result["api_token"] == "***"


def test_redact_and_truncate_marks_large_payload() -> None:
    result = redact_and_truncate({"body": "x" * 200}, max_bytes=80)

    assert result["_truncated"] is True
    assert result["_original_size"] > 80
    assert "body" in result["_preview"]


def test_recorder_creates_and_finishes_span() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")
        span = await recorder.start_span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
            input={"message": "hello"},
            attributes={"started": True},
        )
        await recorder.end_span(
            span,
            output={"done": True},
            attributes={"ended": True},
        )

        stored = repo.spans[span.id]
        assert stored.status is TraceSpanStatus.OK
        assert stored.input == {"message": "hello"}
        assert stored.output == {"done": True}
        assert stored.attributes == {"started": True, "ended": True}
        assert stored.duration_ms is not None

    asyncio.run(scenario())


def test_recorder_write_failure_does_not_raise() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository(fail_writes=True)
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")
        span = await recorder.start_span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        )
        await recorder.end_span(span, error=RuntimeError("boom"))

    asyncio.run(scenario())


def test_recorder_nests_child_spans() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")
        root = await recorder.start_span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        )
        child = await recorder.start_span(
            span_type=TraceSpanType.LLM,
            name="deepseek-chat",
        )
        await recorder.end_span(child)
        await recorder.end_span(root)

        stored_child = repo.spans[child.id]
        assert stored_child.trace_id == root.trace_id
        assert stored_child.parent_span_id == root.id

    asyncio.run(scenario())


def test_recorder_persists_explicit_terminal_status() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")
        span = await recorder.start_span(
            span_type=TraceSpanType.AGENT,
            name="react.execute_step",
        )

        await recorder.end_span(span, status=TraceSpanStatus.WAITING)

        assert repo.spans[span.id].status is TraceSpanStatus.WAITING
        assert repo.spans[span.id].error is None

    asyncio.run(scenario())


def test_recorder_keeps_parallel_task_branches_under_same_flow() -> None:
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
            name="team",
        )

        async def execute(task_id: str) -> tuple[str, str]:
            task = await recorder.start_span(
                span_type=TraceSpanType.TASK,
                name="team.task",
                attributes={"task_id": task_id},
            )
            agent = await recorder.start_span(
                span_type=TraceSpanType.AGENT,
                name="task_worker.execute",
            )
            await asyncio.sleep(0)
            await recorder.end_span(agent)
            await recorder.end_span(task)
            return task.id, agent.id

        branches = await asyncio.gather(execute("task-1"), execute("task-2"))
        await recorder.end_span(flow)
        await recorder.end_span(root)

        for task_id, agent_id in branches:
            stored_task = repo.spans[task_id]
            stored_agent = repo.spans[agent_id]
            assert stored_task.parent_span_id == flow.id
            assert stored_agent.parent_span_id == stored_task.id

    asyncio.run(scenario())


def test_recorder_scope_marks_unhandled_exception_as_error() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")

        with pytest.raises(RuntimeError, match="invalid plan"):
            async with recorder.span(
                span_type=TraceSpanType.AGENT,
                name="planner.create_plan",
            ):
                raise RuntimeError("invalid plan")

        stored = next(iter(repo.spans.values()))
        assert stored.status is TraceSpanStatus.ERROR
        assert stored.error == {
            "type": "RuntimeError",
            "message": "invalid plan",
        }

    asyncio.run(scenario())


def test_recorder_scope_marks_asyncio_cancellation() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")
        entered = asyncio.Event()

        async def execute() -> None:
            async with recorder.span(
                span_type=TraceSpanType.AGENT,
                name="task_worker.execute",
            ):
                entered.set()
                await asyncio.Future()

        task = asyncio.create_task(execute())
        await asyncio.sleep(0)
        if task.done():
            await task
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        stored = next(iter(repo.spans.values()))
        assert stored.status is TraceSpanStatus.CANCELLED
        assert stored.error is None

    asyncio.run(scenario())


def test_recorder_scope_preserves_waiting_when_generator_closes() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")

        async def stream():
            async with recorder.span(
                span_type=TraceSpanType.AGENT,
                name="react.execute_step",
            ) as scope:
                scope.finish(
                    status=TraceSpanStatus.WAITING,
                    output={"reason": "ask_user"},
                )
                yield "waiting"

        iterator = stream()
        assert await anext(iterator) == "waiting"
        await iterator.aclose()

        stored = next(iter(repo.spans.values()))
        assert stored.status is TraceSpanStatus.WAITING
        assert stored.output == {"reason": "ask_user"}
        assert stored.error is None

    asyncio.run(scenario())


def test_recorder_scope_persists_explicit_operation_error() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")

        async with recorder.span(
            span_type=TraceSpanType.AGENT,
            name="react.execute_step",
        ) as scope:
            scope.finish(error={"message": "iteration limit"})

        stored = next(iter(repo.spans.values()))
        assert stored.status is TraceSpanStatus.ERROR
        assert stored.error == {"message": "iteration limit"}

    asyncio.run(scenario())


def test_recorder_scope_applies_active_cancellation_reason() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")

        async def stream():
            async with recorder.span(
                span_type=TraceSpanType.AGENT,
                name="react.execute_step",
            ):
                yield "running"

        iterator = stream()
        assert await anext(iterator) == "running"
        token = recorder.set_cancellation_reason("superseded_by_new_input")
        try:
            await iterator.aclose()
        finally:
            recorder.reset_cancellation_reason(token)

        stored = next(iter(repo.spans.values()))
        assert stored.status is TraceSpanStatus.CANCELLED
        assert stored.attributes["cancellation_reason"] == (
            "superseded_by_new_input"
        )

    asyncio.run(scenario())


def test_recorder_shares_cancellation_reason_with_existing_child_task() -> None:
    async def scenario() -> None:
        repo = FakeTraceRepository()
        recorder = TraceRecorder(lambda: FakeUow(repo), session_id="session-1")
        entered = asyncio.Event()

        async with recorder.span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        ):
            async def execute() -> None:
                async with recorder.span(
                    span_type=TraceSpanType.AGENT,
                    name="task_worker.execute",
                ):
                    entered.set()
                    await asyncio.Future()

            task = asyncio.create_task(execute())
            await entered.wait()
            token = recorder.set_cancellation_reason(
                "superseded_by_new_input"
            )
            try:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                recorder.reset_cancellation_reason(token)

        agent_span = next(
            span for span in repo.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert agent_span.status is TraceSpanStatus.CANCELLED
        assert agent_span.attributes["cancellation_reason"] == (
            "superseded_by_new_input"
        )

    asyncio.run(scenario())
