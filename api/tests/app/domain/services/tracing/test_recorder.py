import asyncio
from dataclasses import dataclass, field

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
