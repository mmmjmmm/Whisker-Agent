import asyncio
from dataclasses import dataclass, field

from app.domain.models.trace import TraceSpan, TraceSpanType
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
