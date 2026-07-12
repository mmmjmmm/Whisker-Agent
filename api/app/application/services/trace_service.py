from collections import defaultdict
from datetime import datetime
from math import ceil
from typing import Callable

from app.application.errors.exceptions import NotFoundError
from app.domain.models.trace import (
    TraceMetrics,
    TraceSpan,
    TraceSpanStatus,
    TraceSpanType,
    TraceSummary,
)
from app.domain.repositories.uow import IUnitOfWork


def _token_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdecimal():
            return int(stripped)
    return 0


class TraceService:
    """Trace read service."""

    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def _ensure_session(self, session_id: str) -> None:
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(session_id)
        if not session:
            raise NotFoundError("该会话不存在，请核实后重试")

    async def list_traces(self, session_id: str) -> list[TraceSummary]:
        await self._ensure_session(session_id)
        async with self._uow_factory() as uow:
            spans = await uow.trace.list_by_session(session_id)
        return self._build_summaries(spans)

    async def get_trace(self, session_id: str, trace_id: str) -> list[TraceSpan]:
        await self._ensure_session(session_id)
        async with self._uow_factory() as uow:
            return await uow.trace.list_by_trace(session_id, trace_id)

    async def get_metrics(self, session_id: str) -> TraceMetrics:
        summaries = await self.list_traces(session_id)
        if not summaries:
            return TraceMetrics()

        durations = sorted(
            summary.duration_ms
            for summary in summaries
            if summary.duration_ms is not None
        )
        error_count = sum(
            1
            for summary in summaries
            if summary.status is TraceSpanStatus.ERROR
        )
        p95_index = max(0, ceil(len(durations) * 0.95) - 1) if durations else 0
        models = sorted({model for summary in summaries for model in summary.models})

        return TraceMetrics(
            trace_count=len(summaries),
            error_trace_count=error_count,
            error_rate=error_count / len(summaries),
            avg_duration_ms=(sum(durations) / len(durations)) if durations else 0.0,
            p95_duration_ms=durations[p95_index] if durations else None,
            llm_call_count=sum(summary.llm_call_count for summary in summaries),
            tool_call_count=sum(summary.tool_call_count for summary in summaries),
            total_tokens=sum(summary.total_tokens for summary in summaries),
            models=models,
        )

    def _build_summaries(self, spans: list[TraceSpan]) -> list[TraceSummary]:
        grouped: dict[str, list[TraceSpan]] = defaultdict(list)
        for span in spans:
            grouped[span.trace_id].append(span)

        summaries = [
            self._build_summary(trace_id, trace_spans)
            for trace_id, trace_spans in grouped.items()
        ]
        return sorted(
            summaries,
            key=lambda item: (item.started_at or datetime.min, item.trace_id),
            reverse=True,
        )

    def _build_summary(
        self,
        trace_id: str,
        trace_spans: list[TraceSpan],
    ) -> TraceSummary:
        ordered = sorted(trace_spans, key=lambda item: (item.started_at, item.id))
        root = next(
            (span for span in ordered if span.span_type is TraceSpanType.ROOT),
            None,
        )
        started_at = ordered[0].started_at if ordered else None
        ended_candidates = [span.ended_at for span in ordered if span.ended_at]
        ended_at = max(ended_candidates) if ended_candidates else None
        duration_ms = root.duration_ms if root and root.duration_ms is not None else None
        if duration_ms is None and started_at and ended_at:
            duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))

        llm_spans = [span for span in ordered if span.span_type is TraceSpanType.LLM]
        tool_spans = [span for span in ordered if span.span_type is TraceSpanType.TOOL]
        error_count = sum(1 for span in ordered if span.status is TraceSpanStatus.ERROR)
        models = sorted(
            {
                str(span.attributes.get("model"))
                for span in llm_spans
                if span.attributes.get("model")
            }
        )
        root_input_preview = ""
        if root and isinstance(root.input.get("message"), str):
            root_input_preview = root.input["message"]

        return TraceSummary(
            trace_id=trace_id,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            status=TraceSpanStatus.ERROR if error_count else TraceSpanStatus.OK,
            root_input_preview=root_input_preview,
            span_count=len(ordered),
            error_count=error_count,
            llm_call_count=len(llm_spans),
            tool_call_count=len(tool_spans),
            models=models,
            prompt_tokens=sum(
                _token_count(span.attributes.get("prompt_tokens"))
                for span in llm_spans
            ),
            completion_tokens=sum(
                _token_count(span.attributes.get("completion_tokens"))
                for span in llm_spans
            ),
            total_tokens=sum(
                _token_count(span.attributes.get("total_tokens"))
                for span in llm_spans
            ),
        )
