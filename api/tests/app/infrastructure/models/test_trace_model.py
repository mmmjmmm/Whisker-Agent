from app.domain.models.trace import TraceSpan, TraceSpanStatus, TraceSpanType
from app.infrastructure.models.trace import TraceSpanModel


def test_trace_span_model_round_trips_domain() -> None:
    span = TraceSpan(
        id="span-1",
        trace_id="trace-1",
        session_id="session-1",
        parent_span_id="parent-1",
        span_type=TraceSpanType.LLM,
        name="deepseek-chat",
        status=TraceSpanStatus.OK,
        duration_ms=123,
        input={"message_count": 2},
        output={"content": "done"},
        error=None,
        attributes={"model": "deepseek-chat", "total_tokens": 42},
    )

    model = TraceSpanModel.from_domain(span)
    restored = model.to_domain()

    assert restored.id == "span-1"
    assert restored.trace_id == "trace-1"
    assert restored.span_type is TraceSpanType.LLM
    assert restored.status is TraceSpanStatus.OK
    assert restored.input == {"message_count": 2}
    assert restored.output == {"content": "done"}
    assert restored.attributes["total_tokens"] == 42
