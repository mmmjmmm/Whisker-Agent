from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models.trace import TraceSpanStatus, TraceSpanType


class TraceSummaryResponse(BaseModel):
    trace_id: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    status: TraceSpanStatus
    root_input_preview: str = ""
    span_count: int = 0
    error_count: int = 0
    llm_call_count: int = 0
    tool_call_count: int = 0
    models: list[str] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ListTracesResponse(BaseModel):
    traces: list[TraceSummaryResponse]


class TraceSpanResponse(BaseModel):
    id: str
    trace_id: str
    session_id: str
    parent_span_id: str | None = None
    span_type: TraceSpanType
    name: str
    status: TraceSpanStatus
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceDetailResponse(BaseModel):
    trace_id: str
    spans: list[TraceSpanResponse]


class TraceMetricsResponse(BaseModel):
    trace_count: int = 0
    error_trace_count: int = 0
    error_rate: float = 0.0
    avg_duration_ms: float = 0.0
    p95_duration_ms: int | None = None
    llm_call_count: int = 0
    tool_call_count: int = 0
    total_tokens: int = 0
    models: list[str] = Field(default_factory=list)
