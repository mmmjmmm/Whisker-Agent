import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TraceSpanType(str, Enum):
    ROOT = "root"
    FLOW = "flow"
    AGENT = "agent"
    TASK = "task"
    LLM = "llm"
    TOOL = "tool"
    EVENT = "event"


class TraceSpanStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    WAITING = "waiting"
    CANCELLED = "cancelled"


class TraceSpan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str
    session_id: str
    parent_span_id: str | None = None
    span_type: TraceSpanType
    name: str
    status: TraceSpanStatus = TraceSpanStatus.RUNNING
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceSpanHandle(BaseModel):
    id: str
    trace_id: str
    session_id: str
    parent_span_id: str | None = None
    span_type: TraceSpanType
    name: str
    started_at: datetime
    input: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    recorded: bool = True


class TraceSummary(BaseModel):
    trace_id: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    status: TraceSpanStatus = TraceSpanStatus.OK
    root_input_preview: str = ""
    span_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    llm_call_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    models: list[str] = Field(default_factory=list)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class TraceMetrics(BaseModel):
    trace_count: int = Field(default=0, ge=0)
    error_trace_count: int = Field(default=0, ge=0)
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    avg_duration_ms: float = Field(default=0.0, ge=0.0)
    p95_duration_ms: int | None = Field(default=None, ge=0)
    llm_call_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    models: list[str] = Field(default_factory=list)
