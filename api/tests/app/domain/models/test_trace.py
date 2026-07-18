import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.domain.models.trace import (
    TraceMetrics,
    TraceSpan,
    TraceSpanHandle,
    TraceSpanStatus,
    TraceSpanType,
    TraceSummary,
)


def test_trace_span_defaults() -> None:
    span = TraceSpan(
        trace_id="trace-1",
        session_id="session-1",
        span_type=TraceSpanType.ROOT,
        name="chat",
    )

    assert span.id
    uuid.UUID(span.id)
    assert span.trace_id == "trace-1"
    assert span.session_id == "session-1"
    assert span.parent_span_id is None
    assert span.status is TraceSpanStatus.RUNNING
    assert isinstance(span.started_at, datetime)
    assert span.ended_at is None
    assert span.duration_ms is None
    assert span.input == {}
    assert span.output == {}
    assert span.error is None
    assert span.attributes == {}


def test_trace_execution_types_and_terminal_statuses() -> None:
    assert TraceSpanType("task") is TraceSpanType.TASK
    assert TraceSpanStatus("waiting") is TraceSpanStatus.WAITING
    assert TraceSpanStatus("cancelled") is TraceSpanStatus.CANCELLED


def test_trace_summary_and_metrics_shapes() -> None:
    summary = TraceSummary(
        trace_id="trace-1",
        status=TraceSpanStatus.ERROR,
        span_count=3,
        error_count=1,
        llm_call_count=1,
        tool_call_count=1,
        models=["deepseek-chat"],
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )
    metrics = TraceMetrics(
        trace_count=2,
        error_trace_count=1,
        error_rate=0.5,
        avg_duration_ms=125.0,
        p95_duration_ms=200,
        llm_call_count=3,
        tool_call_count=4,
        total_tokens=99,
        models=["deepseek-chat"],
    )

    assert summary.status is TraceSpanStatus.ERROR
    assert summary.models == ["deepseek-chat"]
    assert metrics.error_rate == 0.5
    assert metrics.total_tokens == 99


def test_trace_span_ids_are_unique() -> None:
    first = TraceSpan(
        trace_id="trace-1",
        session_id="session-1",
        span_type=TraceSpanType.ROOT,
        name="chat",
    )
    second = TraceSpan(
        trace_id="trace-2",
        session_id="session-2",
        span_type=TraceSpanType.ROOT,
        name="chat",
    )

    assert first.id != second.id


def test_trace_mutable_defaults_are_isolated() -> None:
    first_span = TraceSpan(
        trace_id="trace-1",
        session_id="session-1",
        span_type=TraceSpanType.ROOT,
        name="chat",
    )
    second_span = TraceSpan(
        trace_id="trace-2",
        session_id="session-2",
        span_type=TraceSpanType.ROOT,
        name="chat",
    )
    first_summary = TraceSummary(trace_id="trace-1")
    second_summary = TraceSummary(trace_id="trace-2")
    first_metrics = TraceMetrics()
    second_metrics = TraceMetrics()

    first_span.input["message"] = "hello"
    first_span.output["answer"] = "hi"
    first_span.attributes["model"] = "deepseek-chat"
    first_summary.models.append("deepseek-chat")
    first_metrics.models.append("deepseek-chat")

    assert second_span.input == {}
    assert second_span.output == {}
    assert second_span.attributes == {}
    assert second_summary.models == []
    assert second_metrics.models == []


def test_trace_span_handle_defaults() -> None:
    started_at = datetime.now()
    handle = TraceSpanHandle(
        id="span-1",
        trace_id="trace-1",
        session_id="session-1",
        span_type=TraceSpanType.LLM,
        name="deepseek",
        started_at=started_at,
    )

    assert handle.id == "span-1"
    assert handle.trace_id == "trace-1"
    assert handle.session_id == "session-1"
    assert handle.parent_span_id is None
    assert handle.span_type is TraceSpanType.LLM
    assert handle.name == "deepseek"
    assert handle.started_at == started_at
    assert handle.input == {}
    assert handle.attributes == {}
    assert handle.recorded is True


def test_trace_span_handle_mutable_defaults_are_isolated() -> None:
    started_at = datetime.now()
    first = TraceSpanHandle(
        id="span-1",
        trace_id="trace-1",
        session_id="session-1",
        span_type=TraceSpanType.LLM,
        name="deepseek",
        started_at=started_at,
    )
    second = TraceSpanHandle(
        id="span-2",
        trace_id="trace-2",
        session_id="session-2",
        span_type=TraceSpanType.TOOL,
        name="search",
        started_at=started_at,
    )

    first.input["message"] = "hello"
    first.attributes["model"] = "deepseek-chat"

    assert second.input == {}
    assert second.attributes == {}


def test_trace_metric_boundaries_accept_zero_one_and_none() -> None:
    span_with_zero_duration = TraceSpan(
        trace_id="trace-1",
        session_id="session-1",
        span_type=TraceSpanType.ROOT,
        name="chat",
        duration_ms=0,
    )
    span_with_none_duration = TraceSpan(
        trace_id="trace-2",
        session_id="session-2",
        span_type=TraceSpanType.ROOT,
        name="chat",
        duration_ms=None,
    )
    summary_with_zero_duration = TraceSummary(trace_id="trace-1", duration_ms=0)
    summary_with_none_duration = TraceSummary(trace_id="trace-2", duration_ms=None)
    metrics_with_zero_values = TraceMetrics(error_rate=0.0, p95_duration_ms=0)
    metrics_with_one_error_rate = TraceMetrics(error_rate=1.0, p95_duration_ms=None)

    assert span_with_zero_duration.duration_ms == 0
    assert span_with_none_duration.duration_ms is None
    assert summary_with_zero_duration.duration_ms == 0
    assert summary_with_none_duration.duration_ms is None
    assert metrics_with_zero_values.error_rate == 0.0
    assert metrics_with_zero_values.p95_duration_ms == 0
    assert metrics_with_one_error_rate.error_rate == 1.0
    assert metrics_with_one_error_rate.p95_duration_ms is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration_ms", -1),
    ],
)
def test_trace_span_rejects_invalid_metric_values(field: str, value: int) -> None:
    kwargs = {
        "trace_id": "trace-1",
        "session_id": "session-1",
        "span_type": TraceSpanType.ROOT,
        "name": "chat",
        field: value,
    }

    with pytest.raises(ValidationError):
        TraceSpan(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration_ms", -1),
        ("span_count", -1),
        ("error_count", -1),
        ("llm_call_count", -1),
        ("tool_call_count", -1),
        ("prompt_tokens", -1),
        ("completion_tokens", -1),
        ("total_tokens", -1),
    ],
)
def test_trace_summary_rejects_invalid_metric_values(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        TraceSummary(trace_id="trace-1", **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_count", -1),
        ("error_trace_count", -1),
        ("error_rate", -0.1),
        ("error_rate", 1.1),
        ("avg_duration_ms", -0.1),
        ("p95_duration_ms", -1),
        ("llm_call_count", -1),
        ("tool_call_count", -1),
        ("total_tokens", -1),
    ],
)
def test_trace_metrics_rejects_invalid_metric_values(
    field: str, value: int | float
) -> None:
    with pytest.raises(ValidationError):
        TraceMetrics(**{field: value})
