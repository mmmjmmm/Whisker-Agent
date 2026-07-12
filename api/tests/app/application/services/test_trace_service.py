import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.application.services.trace_service import TraceService
from app.domain.models.trace import TraceSpan, TraceSpanStatus, TraceSpanType


@dataclass
class FakeTraceRepository:
    spans: list[TraceSpan]

    async def list_by_session(self, session_id: str) -> list[TraceSpan]:
        return [span for span in self.spans if span.session_id == session_id]

    async def list_by_trace(
        self,
        session_id: str,
        trace_id: str,
    ) -> list[TraceSpan]:
        return [
            span
            for span in self.spans
            if span.session_id == session_id and span.trace_id == trace_id
        ]


class FakeSessionRepository:
    async def get_by_id(self, session_id: str):
        return object() if session_id == "session-1" else None


class FakeUow:
    def __init__(self, spans: list[TraceSpan]) -> None:
        self.trace = FakeTraceRepository(spans)
        self.session = FakeSessionRepository()

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


def make_span(
    trace_id: str,
    span_type: TraceSpanType,
    *,
    status: TraceSpanStatus = TraceSpanStatus.OK,
    duration_ms: int = 10,
    attributes: dict | None = None,
    input: dict | None = None,
) -> TraceSpan:
    started = datetime.now()
    return TraceSpan(
        trace_id=trace_id,
        session_id="session-1",
        span_type=span_type,
        name=span_type.value,
        status=status,
        started_at=started,
        ended_at=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        attributes=attributes or {},
        input=input or {},
    )


def test_trace_service_summarizes_traces_and_metrics() -> None:
    async def scenario() -> None:
        spans = [
            make_span(
                "trace-1",
                TraceSpanType.ROOT,
                duration_ms=100,
                input={"message": "hello"},
            ),
            make_span(
                "trace-1",
                TraceSpanType.LLM,
                attributes={
                    "model": "deepseek-chat",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            ),
            make_span("trace-1", TraceSpanType.TOOL),
            make_span(
                "trace-2",
                TraceSpanType.ROOT,
                status=TraceSpanStatus.ERROR,
                duration_ms=200,
            ),
        ]
        service = TraceService(lambda: FakeUow(spans))

        summaries = await service.list_traces("session-1")
        metrics = await service.get_metrics("session-1")

        assert len(summaries) == 2
        assert next(
            summary for summary in summaries
            if summary.trace_id == "trace-1"
        ).total_tokens == 15
        assert metrics.trace_count == 2
        assert metrics.error_trace_count == 1
        assert metrics.error_rate == 0.5
        assert metrics.total_tokens == 15

    asyncio.run(scenario())
