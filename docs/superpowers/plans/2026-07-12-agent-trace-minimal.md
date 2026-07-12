# Agent Trace Minimal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal local trace system that records agent execution spans and exposes debugging metrics for error rate, latency, model usage, and token usage.

**Architecture:** Add one `trace_spans` table and a best-effort `TraceRecorder` that is injected into the existing agent runner, flows, and agents. Keep the current session event stream unchanged; trace APIs derive summaries and metrics from span rows.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Pydantic v2, PostgreSQL JSONB, Next.js, TypeScript, React.

---

## Scope Check

The approved spec covers one subsystem: local trace capture and trace viewing. It explicitly excludes eval, replay, checkpointing, external exporters, and a `trace_runs` table. This plan keeps those exclusions.

## File Structure

Create backend trace domain and persistence files:

- Create `api/app/domain/models/trace.py`: Pydantic trace span, summaries, metrics, enums, and handle model.
- Create `api/app/domain/repositories/trace_repository.py`: repository protocol.
- Modify `api/app/domain/repositories/uow.py`: expose `trace`.
- Create `api/app/infrastructure/models/trace.py`: SQLAlchemy ORM model.
- Modify `api/app/infrastructure/models/__init__.py`: export `TraceSpanModel`.
- Create `api/app/infrastructure/repositories/db_trace_repository.py`: database repository implementation.
- Modify `api/app/infrastructure/repositories/db_uow.py`: instantiate trace repository.
- Create `api/alembic/versions/b7c9d8e2f1a3_create_trace_spans_table.py`: migration.

Create backend recording and API files:

- Create `api/app/domain/services/tracing/__init__.py`
- Create `api/app/domain/services/tracing/recorder.py`: best-effort recorder, redaction, truncation, context stack.
- Modify `api/app/domain/services/agent_task_runner.py`: create root, flow, and event spans.
- Modify `api/app/domain/services/flows/planner_react.py`: pass recorder into planner and react agents.
- Modify `api/app/domain/services/flows/team.py`: pass recorder into team planner, workers, and synthesizer.
- Modify `api/app/domain/services/agents/base.py`: create LLM and tool spans.
- Modify `api/app/infrastructure/external/llm/openai_llm.py`: attach usage metadata to returned message dict.
- Create `api/app/application/services/trace_service.py`: trace summaries, details, metrics.
- Create `api/app/interfaces/schemas/trace.py`: response schemas.
- Modify `api/app/interfaces/service_dependencies.py`: provide `TraceService`.
- Modify `api/app/interfaces/endpoints/session_routes.py`: add read-only trace endpoints.

Create frontend trace files:

- Modify `ui/src/lib/api/types.ts`: add trace types.
- Modify `ui/src/lib/api/session.ts`: add trace API methods.
- Create `ui/src/components/trace-panel.tsx`: minimal trace list and span detail panel.
- Modify `ui/src/components/session-header.tsx`: add trace panel trigger button.
- Modify `ui/src/components/session-detail-view.tsx`: load and show trace panel.

Create tests:

- Create `api/tests/app/domain/models/test_trace.py`
- Create `api/tests/app/domain/services/tracing/test_recorder.py`
- Create `api/tests/app/application/services/test_trace_service.py`
- Create `api/tests/app/domain/services/test_agent_task_runner_tracing.py`
- Create `api/tests/app/interfaces/endpoints/test_trace_routes.py`

Do not stage or commit the unrelated untracked file `docs/agent-skills-implementation.md`.

---

### Task 1: Trace Domain Models and Repository Protocol

**Files:**
- Create: `api/app/domain/models/trace.py`
- Create: `api/app/domain/repositories/trace_repository.py`
- Modify: `api/app/domain/repositories/uow.py`
- Test: `api/tests/app/domain/models/test_trace.py`

- [ ] **Step 1: Write failing domain model tests**

Create `api/tests/app/domain/models/test_trace.py`:

```python
from datetime import datetime

from app.domain.models.trace import (
    TraceSpan,
    TraceSpanStatus,
    TraceSpanType,
    TraceSummary,
    TraceMetrics,
)


def test_trace_span_defaults() -> None:
    span = TraceSpan(
        trace_id="trace-1",
        session_id="session-1",
        span_type=TraceSpanType.ROOT,
        name="chat",
    )

    assert span.id
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd api && pytest tests/app/domain/models/test_trace.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.models.trace'`.

- [ ] **Step 3: Add trace domain models**

Create `api/app/domain/models/trace.py`:

```python
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TraceSpanType(str, Enum):
    ROOT = "root"
    FLOW = "flow"
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    EVENT = "event"


class TraceSpanStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


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
    duration_ms: int | None = None
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
    recorded: bool = True


class TraceSummary(BaseModel):
    trace_id: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    status: TraceSpanStatus = TraceSpanStatus.OK
    root_input_preview: str = ""
    span_count: int = 0
    error_count: int = 0
    llm_call_count: int = 0
    tool_call_count: int = 0
    models: list[str] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class TraceMetrics(BaseModel):
    trace_count: int = 0
    error_trace_count: int = 0
    error_rate: float = 0.0
    avg_duration_ms: float = 0.0
    p95_duration_ms: int | None = None
    llm_call_count: int = 0
    tool_call_count: int = 0
    total_tokens: int = 0
    models: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add repository protocol and UoW property**

Create `api/app/domain/repositories/trace_repository.py`:

```python
from typing import Protocol

from app.domain.models.trace import TraceSpan


class TraceRepository(Protocol):
    async def create_span(self, span: TraceSpan) -> None:
        """Persist a new trace span."""
        ...

    async def finish_span(self, span: TraceSpan) -> None:
        """Persist final span status, output, error, attributes, and timing."""
        ...

    async def list_by_session(self, session_id: str) -> list[TraceSpan]:
        """Return all spans for a session ordered by start time."""
        ...

    async def list_by_trace(self, session_id: str, trace_id: str) -> list[TraceSpan]:
        """Return all spans for a trace ordered by start time."""
        ...
```

Modify `api/app/domain/repositories/uow.py`:

```python
from .trace_repository import TraceRepository
```

and add the property to `IUnitOfWork`:

```python
    trace: TraceRepository
```

- [ ] **Step 5: Run the domain model test**

Run:

```bash
cd api && pytest tests/app/domain/models/test_trace.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add api/app/domain/models/trace.py \
  api/app/domain/repositories/trace_repository.py \
  api/app/domain/repositories/uow.py \
  api/tests/app/domain/models/test_trace.py
git commit -m "feat: add trace domain model"
```

---

### Task 2: Trace Persistence and UoW Wiring

**Files:**
- Create: `api/app/infrastructure/models/trace.py`
- Create: `api/app/infrastructure/repositories/db_trace_repository.py`
- Create: `api/alembic/versions/b7c9d8e2f1a3_create_trace_spans_table.py`
- Modify: `api/app/infrastructure/models/__init__.py`
- Modify: `api/app/infrastructure/repositories/db_uow.py`
- Test: `api/tests/app/infrastructure/models/test_trace_model.py`

- [ ] **Step 1: Write failing ORM mapping test**

Create `api/tests/app/infrastructure/models/test_trace_model.py`:

```python
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
```

- [ ] **Step 2: Run the ORM mapping test to verify it fails**

Run:

```bash
cd api && pytest tests/app/infrastructure/models/test_trace_model.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.infrastructure.models.trace'`.

- [ ] **Step 3: Add SQLAlchemy trace model**

Create `api/app/infrastructure/models/trace.py`:

```python
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.trace import TraceSpan
from app.infrastructure.models.base import Base


class TraceSpanModel(Base):
    __tablename__ = "trace_spans"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    span_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    output: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    @classmethod
    def from_domain(cls, span: TraceSpan) -> "TraceSpanModel":
        return cls(**span.model_dump(mode="json"))

    def to_domain(self) -> TraceSpan:
        return TraceSpan.model_validate(self, from_attributes=True)

    def update_from_domain(self, span: TraceSpan) -> None:
        for field, value in span.model_dump(mode="json").items():
            setattr(self, field, value)
```

Modify `api/app/infrastructure/models/__init__.py`:

```python
from .trace import TraceSpanModel
```

and update `__all__`:

```python
__all__ = ["Base", "SessionModel", "FileModel", "SkillModel", "TraceSpanModel"]
```

- [ ] **Step 4: Add database repository**

Create `api/app/infrastructure/repositories/db_trace_repository.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.trace import TraceSpan
from app.domain.repositories.trace_repository import TraceRepository
from app.infrastructure.models import TraceSpanModel


class DBTraceRepository(TraceRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def create_span(self, span: TraceSpan) -> None:
        self.db_session.add(TraceSpanModel.from_domain(span))

    async def finish_span(self, span: TraceSpan) -> None:
        stmt = select(TraceSpanModel).where(TraceSpanModel.id == span.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(TraceSpanModel.from_domain(span))
            return
        record.update_from_domain(span)

    async def list_by_session(self, session_id: str) -> list[TraceSpan]:
        stmt = (
            select(TraceSpanModel)
            .where(TraceSpanModel.session_id == session_id)
            .order_by(TraceSpanModel.started_at.asc(), TraceSpanModel.id.asc())
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def list_by_trace(self, session_id: str, trace_id: str) -> list[TraceSpan]:
        stmt = (
            select(TraceSpanModel)
            .where(
                TraceSpanModel.session_id == session_id,
                TraceSpanModel.trace_id == trace_id,
            )
            .order_by(TraceSpanModel.started_at.asc(), TraceSpanModel.id.asc())
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]
```

Modify `api/app/infrastructure/repositories/db_uow.py`:

```python
from .db_trace_repository import DBTraceRepository
```

and inside `DBUnitOfWork.__aenter__` after `self.skill = ...`:

```python
        self.trace = DBTraceRepository(db_session=self.db_session)
```

- [ ] **Step 5: Add Alembic migration**

Create `api/alembic/versions/b7c9d8e2f1a3_create_trace_spans_table.py`:

```python
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b7c9d8e2f1a3"
down_revision: Union[str, None] = "9a4f6c2e1d70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trace_spans",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("parent_span_id", sa.String(length=255), nullable=True),
        sa.Column("span_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trace_spans_id"),
    )
    op.create_index("ix_trace_spans_session_started", "trace_spans", ["session_id", "started_at"])
    op.create_index("ix_trace_spans_session_trace", "trace_spans", ["session_id", "trace_id"])
    op.create_index("ix_trace_spans_trace_parent", "trace_spans", ["trace_id", "parent_span_id"])
    op.create_index("ix_trace_spans_span_type", "trace_spans", ["span_type"])
    op.create_index("ix_trace_spans_status", "trace_spans", ["status"])


def downgrade() -> None:
    op.drop_index("ix_trace_spans_status", table_name="trace_spans")
    op.drop_index("ix_trace_spans_span_type", table_name="trace_spans")
    op.drop_index("ix_trace_spans_trace_parent", table_name="trace_spans")
    op.drop_index("ix_trace_spans_session_trace", table_name="trace_spans")
    op.drop_index("ix_trace_spans_session_started", table_name="trace_spans")
    op.drop_table("trace_spans")
```

- [ ] **Step 6: Run ORM mapping test and migration syntax check**

Run:

```bash
cd api && pytest tests/app/infrastructure/models/test_trace_model.py -v
cd api && alembic heads
```

Expected: test PASS; Alembic prints one head revision.

- [ ] **Step 7: Commit Task 2**

```bash
git add api/app/infrastructure/models/trace.py \
  api/app/infrastructure/models/__init__.py \
  api/app/infrastructure/repositories/db_trace_repository.py \
  api/app/infrastructure/repositories/db_uow.py \
  api/alembic/versions/*_create_trace_spans_table.py \
  api/tests/app/infrastructure/models/test_trace_model.py
git commit -m "feat: persist trace spans"
```

---

### Task 3: Trace Recorder, Redaction, and Truncation

**Files:**
- Create: `api/app/domain/services/tracing/__init__.py`
- Create: `api/app/domain/services/tracing/recorder.py`
- Test: `api/tests/app/domain/services/tracing/test_recorder.py`

- [ ] **Step 1: Write failing recorder tests**

Create `api/tests/app/domain/services/tracing/test_recorder.py`:

```python
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
        return [span for span in self.spans.values() if span.session_id == session_id]

    async def list_by_trace(self, session_id: str, trace_id: str) -> list[TraceSpan]:
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
            "nested": {"Authorization": "Bearer token", "safe": "value"},
        },
        max_bytes=1024,
    )

    assert result["api_key"] == "***"
    assert result["nested"]["Authorization"] == "***"
    assert result["nested"]["safe"] == "value"


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
        )
        await recorder.end_span(span, output={"done": True})

        stored = repo.spans[span.id]
        assert stored.status is TraceSpanStatus.OK
        assert stored.output == {"done": True}
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
```

- [ ] **Step 2: Run recorder tests to verify they fail**

Run:

```bash
cd api && pytest tests/app/domain/services/tracing/test_recorder.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.services.tracing'`.

- [ ] **Step 3: Add tracing package and recorder**

Create `api/app/domain/services/tracing/__init__.py`:

```python
from .recorder import TraceRecorder, redact_and_truncate

__all__ = ["TraceRecorder", "redact_and_truncate"]
```

Create `api/app/domain/services/tracing/recorder.py`:

```python
import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Callable

from app.domain.models.trace import (
    TraceSpan,
    TraceSpanHandle,
    TraceSpanStatus,
    TraceSpanType,
)
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PARTS = (
    "api_key",
    "token",
    "password",
    "secret",
    "authorization",
)
DEFAULT_MAX_PAYLOAD_BYTES = 20 * 1024
_span_stack: ContextVar[tuple[TraceSpanHandle, ...]] = ContextVar(
    "trace_span_stack",
    default=(),
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lower_key = str(key).lower()
            if any(part in lower_key for part in SENSITIVE_KEY_PARTS):
                redacted[key] = "***"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def redact_and_truncate(
    value: Any,
    *,
    max_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> dict[str, Any]:
    redacted = _redact(value)
    if not isinstance(redacted, dict):
        redacted = {"value": redacted}

    encoded = json.dumps(redacted, ensure_ascii=False, default=str)
    size = len(encoded.encode("utf-8"))
    if size <= max_bytes:
        return redacted

    preview = encoded.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return {
        "_truncated": True,
        "_original_size": size,
        "_preview": preview,
    }


def _error_payload(error: BaseException | dict[str, Any] | str | None) -> dict[str, Any] | None:
    if error is None:
        return None
    if isinstance(error, dict):
        return redact_and_truncate(error)
    if isinstance(error, BaseException):
        return {
            "type": type(error).__name__,
            "message": str(error),
        }
    return {
        "type": "Error",
        "message": str(error),
    }


class TraceRecorder:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        *,
        session_id: str,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> None:
        self._uow_factory = uow_factory
        self._session_id = session_id
        self._max_payload_bytes = max_payload_bytes

    async def start_span(
        self,
        *,
        span_type: TraceSpanType,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        input: Any = None,
        attributes: dict[str, Any] | None = None,
    ) -> TraceSpanHandle:
        stack = _span_stack.get()
        parent = parent_span_id if parent_span_id is not None else (stack[-1].id if stack else None)
        resolved_trace_id = trace_id or (stack[-1].trace_id if stack else str(uuid.uuid4()))
        span = TraceSpan(
            trace_id=resolved_trace_id,
            session_id=self._session_id,
            parent_span_id=parent,
            span_type=span_type,
            name=name,
            input=redact_and_truncate(input or {}, max_bytes=self._max_payload_bytes),
            attributes=redact_and_truncate(attributes or {}, max_bytes=self._max_payload_bytes),
        )
        recorded = True
        try:
            async with self._uow_factory() as uow:
                await uow.trace.create_span(span)
        except Exception as exc:
            recorded = False
            logger.warning("trace span start failed: %s", exc)

        handle = TraceSpanHandle(
            id=span.id,
            trace_id=span.trace_id,
            session_id=span.session_id,
            parent_span_id=span.parent_span_id,
            span_type=span.span_type,
            name=span.name,
            started_at=span.started_at,
            recorded=recorded,
        )
        _span_stack.set((*stack, handle))
        return handle

    async def end_span(
        self,
        span: TraceSpanHandle | None,
        *,
        output: Any = None,
        error: BaseException | dict[str, Any] | str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if span is None:
            return

        ended_at = datetime.now()
        error_data = _error_payload(error)
        status = TraceSpanStatus.ERROR if error_data else TraceSpanStatus.OK
        finished = TraceSpan(
            id=span.id,
            trace_id=span.trace_id,
            session_id=span.session_id,
            parent_span_id=span.parent_span_id,
            span_type=span.span_type,
            name=span.name,
            status=status,
            started_at=span.started_at,
            ended_at=ended_at,
            duration_ms=max(0, int((ended_at - span.started_at).total_seconds() * 1000)),
            output=redact_and_truncate(output or {}, max_bytes=self._max_payload_bytes),
            error=error_data,
            attributes=redact_and_truncate(attributes or {}, max_bytes=self._max_payload_bytes),
        )
        try:
            if span.recorded:
                async with self._uow_factory() as uow:
                    await uow.trace.finish_span(finished)
        except Exception as exc:
            logger.warning("trace span finish failed: %s", exc)
        finally:
            stack = _span_stack.get()
            if stack and stack[-1].id == span.id:
                _span_stack.set(stack[:-1])
            else:
                _span_stack.set(tuple(item for item in stack if item.id != span.id))
```

- [ ] **Step 4: Run recorder tests**

Run:

```bash
cd api && pytest tests/app/domain/services/tracing/test_recorder.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add api/app/domain/services/tracing/__init__.py \
  api/app/domain/services/tracing/recorder.py \
  api/tests/app/domain/services/tracing/test_recorder.py
git commit -m "feat: add trace recorder"
```

---

### Task 4: Instrument Agent Execution

**Files:**
- Modify: `api/app/domain/services/agent_task_runner.py`
- Modify: `api/app/domain/services/flows/planner_react.py`
- Modify: `api/app/domain/services/flows/team.py`
- Modify: `api/app/domain/services/agents/base.py`
- Modify: `api/app/infrastructure/external/llm/openai_llm.py`
- Test: `api/tests/app/domain/services/test_agent_task_runner_tracing.py`

- [ ] **Step 1: Write failing instrumentation test**

Create `api/tests/app/domain/services/test_agent_task_runner_tracing.py`:

```python
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
        return [span for span in self.spans.values() if span.session_id == session_id]

    async def list_by_trace(self, session_id: str, trace_id: str) -> list[TraceSpan]:
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
        flow = await recorder.start_span(span_type=TraceSpanType.FLOW, name="planner_react")
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
        assert next(span for span in spans if span.span_type is TraceSpanType.FLOW).parent_span_id == root.id
        assert next(span for span in spans if span.span_type is TraceSpanType.LLM).parent_span_id == flow.id

    asyncio.run(scenario())
```

- [ ] **Step 2: Run instrumentation test**

Run:

```bash
cd api && pytest tests/app/domain/services/test_agent_task_runner_tracing.py -v
```

Expected: PASS after Task 3. This test protects context nesting before touching the agent runner.

- [ ] **Step 3: Attach usage metadata in OpenAI LLM wrapper**

Modify `api/app/infrastructure/external/llm/openai_llm.py` inside `invoke()` after the response is returned and before returning the message dict:

```python
            message = response.choices[0].message.model_dump()
            if response.usage:
                message["_usage"] = response.usage.model_dump()
            return message
```

Replace the existing line:

```python
            return response.choices[0].message.model_dump()
```

- [ ] **Step 4: Add recorder dependency to BaseAgent**

Modify imports in `api/app/domain/services/agents/base.py`:

```python
from app.domain.models.trace import TraceSpanType
from app.domain.services.tracing import TraceRecorder
```

Modify `BaseAgent.__init__` signature by adding:

```python
            trace_recorder: Optional[TraceRecorder] = None,
```

and store it:

```python
        self._trace_recorder = trace_recorder
```

- [ ] **Step 5: Instrument `_invoke_llm()`**

In `api/app/domain/services/agents/base.py`, wrap the LLM call in `_invoke_llm()` with this structure:

```python
                llm_span = None
                if self._trace_recorder:
                    llm_span = await self._trace_recorder.start_span(
                        span_type=TraceSpanType.LLM,
                        name=self._llm.model_name,
                        input={
                            "message_count": len(self._memory.get_messages()),
                            "new_message_count": len(messages),
                        },
                        attributes={
                            "agent_name": self.name,
                            "model": self._llm.model_name,
                            "temperature": self._llm.temperature,
                            "max_tokens": self._llm.max_tokens,
                            "tool_count": len(self._get_available_tools()),
                            "response_format": response_format,
                        },
                    )
                try:
                    message = await self._llm.invoke(
                        messages=self._memory.get_messages(),
                        tools=self._get_available_tools(),
                        response_format=response_format,
                        tool_choice=self._tool_choice,
                    )
                except Exception as exc:
                    if self._trace_recorder:
                        await self._trace_recorder.end_span(llm_span, error=exc)
                    raise

                usage = message.get("_usage") or {}
                if self._trace_recorder:
                    await self._trace_recorder.end_span(
                        llm_span,
                        output={
                            "role": message.get("role"),
                            "content": message.get("content"),
                            "tool_calls": message.get("tool_calls"),
                        },
                        attributes={
                            "agent_name": self.name,
                            "model": self._llm.model_name,
                            "prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens"),
                            "total_tokens": usage.get("total_tokens"),
                        },
                    )
```

Keep the existing filtering and memory update logic after this block. Do not add `_usage` to memory.

- [ ] **Step 6: Instrument `_invoke_tool()`**

Modify imports in `api/app/domain/services/agents/base.py` already added in Step 4. In `_invoke_tool()`, wrap each attempt:

```python
        for _ in range(self._agent_config.max_retries):
            tool_span = None
            if self._trace_recorder:
                tool_span = await self._trace_recorder.start_span(
                    span_type=TraceSpanType.TOOL,
                    name=tool_name,
                    input=arguments,
                    attributes={
                        "agent_name": self.name,
                        "tool_package": tool.name,
                        "function_name": tool_name,
                    },
                )
            try:
                result = await tool.invoke(tool_name, **arguments)
                if self._trace_recorder:
                    await self._trace_recorder.end_span(
                        tool_span,
                        output=result.model_dump(mode="json"),
                        error=None if result.success else {"message": result.message or "tool returned failure"},
                        attributes={
                            "agent_name": self.name,
                            "tool_package": tool.name,
                            "function_name": tool_name,
                            "success": result.success,
                        },
                    )
                return result
            except Exception as e:
                err = str(e)
                if self._trace_recorder:
                    await self._trace_recorder.end_span(
                        tool_span,
                        error=e,
                        attributes={
                            "agent_name": self.name,
                            "tool_package": tool.name,
                            "function_name": tool_name,
                            "success": False,
                        },
                    )
                logger.exception(f"调用工具[{tool_name}]出错, 错误: {str(e)}")
                await asyncio.sleep(self._retry_interval)
                continue
```

Keep the existing final return:

```python
        return ToolResult(success=False, message=err)
```

- [ ] **Step 7: Inject recorder through runner and flows**

Modify `api/app/domain/services/agent_task_runner.py` imports:

```python
import uuid
from app.domain.models.trace import TraceSpanType
from app.domain.services.tracing import TraceRecorder
```

In `AgentTaskRunner.__init__`, create:

```python
        self._trace_recorder = TraceRecorder(
            uow_factory=uow_factory,
            session_id=session_id,
        )
```

Pass `trace_recorder=self._trace_recorder` into `PlannerReActFlow(...)` and `build_team_flow(...)`.

Modify `api/app/domain/services/flows/planner_react.py` constructor signature:

```python
            trace_recorder: TraceRecorder,
```

and pass it into `PlannerAgent(...)` and `ReActAgent(...)`:

```python
            trace_recorder=trace_recorder,
```

Modify `api/app/domain/services/flows/team.py` `build_team_flow(...)` signature:

```python
    trace_recorder,
```

and pass `trace_recorder=trace_recorder` into `TeamPlannerAgent`, `TaskWorker`, and `TeamSynthesizerAgent`.

- [ ] **Step 8: Create root, flow, and event spans**

In `AgentTaskRunner.invoke()`, after determining `message` and `mode`, start the root span:

```python
                root_span = await self._trace_recorder.start_span(
                    span_type=TraceSpanType.ROOT,
                    name="chat",
                    trace_id=str(uuid.uuid4()),
                    input={
                        "message": message[:500],
                        "attachment_count": len(event.attachments),
                        "agent_mode": mode.value,
                    },
                    attributes={
                        "session_id": self._session_id,
                        "agent_mode": mode.value,
                    },
                )
```

Wrap the `_run_flow(...)` iteration in a `try`/`except`/`finally` block that ends the root span. Track `root_error` when an `ErrorEvent` is produced:

```python
                root_error = None
                try:
                    async with aclosing(
                        self._run_flow(message_obj, mode)
                    ) as flow_events:
                        async for event in flow_events:
                            if isinstance(event, ErrorEvent):
                                root_error = {"message": event.error}
                            await self._put_and_add_event(task, event)
                            ...
                except Exception as exc:
                    root_error = exc
                    raise
                finally:
                    await self._trace_recorder.end_span(
                        root_span,
                        error=root_error,
                        attributes={
                            "session_id": self._session_id,
                            "agent_mode": mode.value,
                        },
                    )
```

Preserve the existing title/message/wait status handling inside the loop.

In `_run_flow()`, start and end a flow span:

```python
        flow_span = await self._trace_recorder.start_span(
            span_type=TraceSpanType.FLOW,
            name=mode.value,
            attributes={"agent_mode": mode.value},
        )
        flow_error = None
        try:
            async with aclosing(self._active_flow.invoke(message)) as flow_events:
                async for event in flow_events:
                    if isinstance(event, ErrorEvent):
                        flow_error = {"message": event.error}
                    if isinstance(event, ToolEvent):
                        await self._handle_tool_event(event)
                    elif isinstance(event, MessageEvent):
                        await self._sync_message_attachments_to_storage(event)
                    yield event
        except Exception as exc:
            flow_error = exc
            raise
        finally:
            await self._trace_recorder.end_span(flow_span, error=flow_error)
```

In `_put_and_add_event()`, after assigning `event.id`, create and end an event span:

```python
        event_span = await self._trace_recorder.start_span(
            span_type=TraceSpanType.EVENT,
            name=event.type,
            attributes={
                "event_id": event.id,
                "event_type": event.type,
                "tool_call_id": getattr(event, "tool_call_id", None),
                "graph_id": getattr(event, "graph_id", None),
                "task_id": getattr(event, "task_id", None),
                "agent_id": getattr(event, "agent_id", None),
                "attempt": getattr(event, "attempt", None),
            },
        )
        await self._trace_recorder.end_span(event_span)
```

- [ ] **Step 9: Run existing agent and skill tests**

Run:

```bash
cd api && pytest tests/app/domain/services/test_agent_task_runner_skills.py \
  tests/app/domain/services/flows/test_planner_react_skills.py \
  tests/app/domain/services/flows/test_team_skills.py \
  tests/app/domain/services/test_agent_task_runner_tracing.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit Task 4**

```bash
git add api/app/domain/services/agent_task_runner.py \
  api/app/domain/services/flows/planner_react.py \
  api/app/domain/services/flows/team.py \
  api/app/domain/services/agents/base.py \
  api/app/infrastructure/external/llm/openai_llm.py \
  api/tests/app/domain/services/test_agent_task_runner_tracing.py
git commit -m "feat: instrument agent traces"
```

---

### Task 5: Trace Service, Schemas, and Read APIs

**Files:**
- Create: `api/app/application/services/trace_service.py`
- Create: `api/app/interfaces/schemas/trace.py`
- Modify: `api/app/interfaces/service_dependencies.py`
- Modify: `api/app/interfaces/endpoints/session_routes.py`
- Test: `api/tests/app/application/services/test_trace_service.py`
- Test: `api/tests/app/interfaces/endpoints/test_trace_routes.py`

- [ ] **Step 1: Write failing trace service tests**

Create `api/tests/app/application/services/test_trace_service.py`:

```python
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

    async def list_by_trace(self, session_id: str, trace_id: str) -> list[TraceSpan]:
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
    )


def test_trace_service_summarizes_traces_and_metrics() -> None:
    async def scenario() -> None:
        spans = [
            make_span("trace-1", TraceSpanType.ROOT, duration_ms=100),
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
            make_span("trace-2", TraceSpanType.ROOT, status=TraceSpanStatus.ERROR, duration_ms=200),
        ]
        service = TraceService(lambda: FakeUow(spans))

        summaries = await service.list_traces("session-1")
        metrics = await service.get_metrics("session-1")

        assert len(summaries) == 2
        assert summaries[0].trace_id == "trace-1"
        assert summaries[0].total_tokens == 15
        assert metrics.trace_count == 2
        assert metrics.error_trace_count == 1
        assert metrics.error_rate == 0.5
        assert metrics.total_tokens == 15

    asyncio.run(scenario())
```

- [ ] **Step 2: Run trace service tests to verify they fail**

Run:

```bash
cd api && pytest tests/app/application/services/test_trace_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.application.services.trace_service'`.

- [ ] **Step 3: Add trace service**

Create `api/app/application/services/trace_service.py`:

```python
from collections import defaultdict
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


class TraceService:
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
        error_count = sum(1 for summary in summaries if summary.status is TraceSpanStatus.ERROR)
        total_duration = sum(durations)
        p95_index = max(0, ceil(len(durations) * 0.95) - 1) if durations else 0
        models = sorted({model for summary in summaries for model in summary.models})

        return TraceMetrics(
            trace_count=len(summaries),
            error_trace_count=error_count,
            error_rate=error_count / len(summaries),
            avg_duration_ms=(total_duration / len(durations)) if durations else 0.0,
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

        summaries = []
        for trace_id, trace_spans in grouped.items():
            ordered = sorted(trace_spans, key=lambda item: (item.started_at, item.id))
            root = next((span for span in ordered if span.span_type is TraceSpanType.ROOT), None)
            started_at = ordered[0].started_at if ordered else None
            ended_candidates = [span.ended_at for span in ordered if span.ended_at is not None]
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

            summaries.append(
                TraceSummary(
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
                    prompt_tokens=sum(int(span.attributes.get("prompt_tokens") or 0) for span in llm_spans),
                    completion_tokens=sum(int(span.attributes.get("completion_tokens") or 0) for span in llm_spans),
                    total_tokens=sum(int(span.attributes.get("total_tokens") or 0) for span in llm_spans),
                )
            )

        return sorted(summaries, key=lambda item: item.started_at or item.trace_id, reverse=True)
```

- [ ] **Step 4: Add interface schemas**

Create `api/app/interfaces/schemas/trace.py`:

```python
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
```

- [ ] **Step 5: Wire dependency and routes**

Modify `api/app/interfaces/service_dependencies.py` imports:

```python
from app.application.services.trace_service import TraceService
```

Add:

```python
def get_trace_service() -> TraceService:
    return TraceService(uow_factory=get_uow)
```

Modify `api/app/interfaces/endpoints/session_routes.py` imports:

```python
from app.application.services.trace_service import TraceService
from app.interfaces.schemas.trace import (
    ListTracesResponse,
    TraceDetailResponse,
    TraceMetricsResponse,
    TraceSpanResponse,
    TraceSummaryResponse,
)
from app.interfaces.service_dependencies import get_trace_service
```

Add these routes before the file routes:

```python
@router.get(
    path="/{session_id}/traces",
    response_model=Response[ListTracesResponse],
    summary="获取指定会话 Trace 列表",
)
async def get_session_traces(
        session_id: str,
        trace_service: TraceService = Depends(get_trace_service),
) -> Response[ListTracesResponse]:
    traces = await trace_service.list_traces(session_id)
    return Response.success(
        msg="获取 Trace 列表成功",
        data=ListTracesResponse(
            traces=[TraceSummaryResponse.model_validate(item) for item in traces]
        ),
    )


@router.get(
    path="/{session_id}/traces/{trace_id}",
    response_model=Response[TraceDetailResponse],
    summary="获取指定 Trace 详情",
)
async def get_session_trace_detail(
        session_id: str,
        trace_id: str,
        trace_service: TraceService = Depends(get_trace_service),
) -> Response[TraceDetailResponse]:
    spans = await trace_service.get_trace(session_id, trace_id)
    return Response.success(
        msg="获取 Trace 详情成功",
        data=TraceDetailResponse(
            trace_id=trace_id,
            spans=[TraceSpanResponse.model_validate(span) for span in spans],
        ),
    )


@router.get(
    path="/{session_id}/trace-metrics",
    response_model=Response[TraceMetricsResponse],
    summary="获取指定会话 Trace 指标",
)
async def get_session_trace_metrics(
        session_id: str,
        trace_service: TraceService = Depends(get_trace_service),
) -> Response[TraceMetricsResponse]:
    metrics = await trace_service.get_metrics(session_id)
    return Response.success(
        msg="获取 Trace 指标成功",
        data=TraceMetricsResponse.model_validate(metrics),
    )
```

- [ ] **Step 6: Run service tests**

Run:

```bash
cd api && pytest tests/app/application/services/test_trace_service.py -v
```

Expected: PASS.

- [ ] **Step 7: Add endpoint smoke tests**

Create `api/tests/app/interfaces/endpoints/test_trace_routes.py`:

```python
from app.application.services.trace_service import TraceService
from app.domain.models.trace import TraceMetrics, TraceSpan, TraceSpanStatus, TraceSpanType, TraceSummary
from app.interfaces.service_dependencies import get_trace_service
from app.main import app


class FakeTraceService:
    async def list_traces(self, session_id: str):
        return [
            TraceSummary(
                trace_id="trace-1",
                status=TraceSpanStatus.OK,
                span_count=1,
                models=["deepseek-chat"],
                total_tokens=12,
            )
        ]

    async def get_trace(self, session_id: str, trace_id: str):
        return [
            TraceSpan(
                id="span-1",
                trace_id=trace_id,
                session_id=session_id,
                span_type=TraceSpanType.ROOT,
                name="chat",
                status=TraceSpanStatus.OK,
            )
        ]

    async def get_metrics(self, session_id: str):
        return TraceMetrics(
            trace_count=1,
            error_trace_count=0,
            error_rate=0.0,
            total_tokens=12,
            models=["deepseek-chat"],
        )


def test_trace_routes(client):
    app.dependency_overrides[get_trace_service] = lambda: FakeTraceService()
    try:
        traces = client.get("/api/sessions/session-1/traces")
        detail = client.get("/api/sessions/session-1/traces/trace-1")
        metrics = client.get("/api/sessions/session-1/trace-metrics")
    finally:
        app.dependency_overrides.pop(get_trace_service, None)

    assert traces.status_code == 200
    assert traces.json()["data"]["traces"][0]["trace_id"] == "trace-1"
    assert detail.status_code == 200
    assert detail.json()["data"]["spans"][0]["id"] == "span-1"
    assert metrics.status_code == 200
    assert metrics.json()["data"]["trace_count"] == 1
```

- [ ] **Step 8: Run endpoint tests**

Run:

```bash
cd api && pytest tests/app/interfaces/endpoints/test_trace_routes.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 5**

```bash
git add api/app/application/services/trace_service.py \
  api/app/interfaces/schemas/trace.py \
  api/app/interfaces/service_dependencies.py \
  api/app/interfaces/endpoints/session_routes.py \
  api/tests/app/application/services/test_trace_service.py \
  api/tests/app/interfaces/endpoints/test_trace_routes.py
git commit -m "feat: expose trace APIs"
```

---

### Task 6: Minimal Trace UI

**Files:**
- Modify: `ui/src/lib/api/types.ts`
- Modify: `ui/src/lib/api/session.ts`
- Create: `ui/src/components/trace-panel.tsx`
- Modify: `ui/src/components/session-header.tsx`
- Modify: `ui/src/components/session-detail-view.tsx`

- [ ] **Step 1: Add frontend API types**

Modify `ui/src/lib/api/types.ts` by adding:

```ts
export type TraceSpanStatus = "running" | "ok" | "error";

export type TraceSpanType =
  | "root"
  | "flow"
  | "agent"
  | "llm"
  | "tool"
  | "event";

export type TraceSummary = {
  trace_id: string;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  status: TraceSpanStatus;
  root_input_preview: string;
  span_count: number;
  error_count: number;
  llm_call_count: number;
  tool_call_count: number;
  models: string[];
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
};

export type TraceSpan = {
  id: string;
  trace_id: string;
  session_id: string;
  parent_span_id?: string | null;
  span_type: TraceSpanType;
  name: string;
  status: TraceSpanStatus;
  started_at: string;
  ended_at?: string | null;
  duration_ms?: number | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  error?: Record<string, unknown> | null;
  attributes: Record<string, unknown>;
};

export type TraceMetrics = {
  trace_count: number;
  error_trace_count: number;
  error_rate: number;
  avg_duration_ms: number;
  p95_duration_ms?: number | null;
  llm_call_count: number;
  tool_call_count: number;
  total_tokens: number;
  models: string[];
};

export type TraceListData = {
  traces: TraceSummary[];
};

export type TraceDetailData = {
  trace_id: string;
  spans: TraceSpan[];
};
```

- [ ] **Step 2: Add frontend API methods**

Modify `ui/src/lib/api/session.ts` imports:

```ts
  TraceListData,
  TraceDetailData,
  TraceMetrics,
```

Add methods to `sessionApi`:

```ts
  getSessionTraces: (sessionId: string): Promise<TraceListData> => {
    return get<TraceListData>(`/sessions/${sessionId}/traces`);
  },

  getSessionTraceDetail: (
    sessionId: string,
    traceId: string
  ): Promise<TraceDetailData> => {
    return get<TraceDetailData>(`/sessions/${sessionId}/traces/${traceId}`);
  },

  getSessionTraceMetrics: (sessionId: string): Promise<TraceMetrics> => {
    return get<TraceMetrics>(`/sessions/${sessionId}/trace-metrics`);
  },
```

- [ ] **Step 3: Add TracePanel component**

Create `ui/src/components/trace-panel.tsx`:

```tsx
'use client'

import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertCircle, Clock, Database, X } from 'lucide-react'
import { sessionApi } from '@/lib/api/session'
import type { TraceDetailData, TraceMetrics, TraceSpan, TraceSummary } from '@/lib/api/types'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'

export interface TracePanelProps {
  sessionId: string
  onClose: () => void
}

function formatMs(value?: number | null) {
  if (value === null || value === undefined) return '-'
  if (value < 1000) return `${value}ms`
  return `${(value / 1000).toFixed(2)}s`
}

function formatJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2)
}

function buildChildren(spans: TraceSpan[]) {
  const map = new Map<string | null, TraceSpan[]>()
  for (const span of spans) {
    const key = span.parent_span_id ?? null
    const list = map.get(key) ?? []
    list.push(span)
    map.set(key, list)
  }
  return map
}

function SpanTree({
  spans,
  selectedId,
  onSelect,
}: {
  spans: TraceSpan[]
  selectedId?: string
  onSelect: (span: TraceSpan) => void
}) {
  const children = useMemo(() => buildChildren(spans), [spans])

  const renderNode = (span: TraceSpan, depth: number) => {
    const isSelected = span.id === selectedId
    return (
      <div key={span.id}>
        <button
          type="button"
          onClick={() => onSelect(span)}
          className={`w-full text-left px-2 py-1.5 text-xs rounded border transition-colors ${
            isSelected ? 'bg-gray-900 text-white border-gray-900' : 'bg-white hover:bg-gray-50 border-gray-200'
          }`}
          style={{ marginLeft: depth * 12 }}
        >
          <span className={span.status === 'error' ? 'text-red-500' : ''}>
            {span.span_type} · {span.name} · {formatMs(span.duration_ms)}
          </span>
        </button>
        {(children.get(span.id) ?? []).map((child) => renderNode(child, depth + 1))}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1">
      {(children.get(null) ?? spans.filter((span) => !span.parent_span_id)).map((span) => renderNode(span, 0))}
    </div>
  )
}

export function TracePanel({ sessionId, onClose }: TracePanelProps) {
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [metrics, setMetrics] = useState<TraceMetrics | null>(null)
  const [detail, setDetail] = useState<TraceDetailData | null>(null)
  const [selectedSpan, setSelectedSpan] = useState<TraceSpan | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      const [traceList, traceMetrics] = await Promise.all([
        sessionApi.getSessionTraces(sessionId),
        sessionApi.getSessionTraceMetrics(sessionId),
      ])
      if (cancelled) return
      setTraces(traceList.traces)
      setMetrics(traceMetrics)
      if (traceList.traces[0]) {
        const traceDetail = await sessionApi.getSessionTraceDetail(sessionId, traceList.traces[0].trace_id)
        if (cancelled) return
        setDetail(traceDetail)
        setSelectedSpan(traceDetail.spans[0] ?? null)
      }
      setLoading(false)
    }
    load().catch(() => setLoading(false))
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const loadTrace = async (traceId: string) => {
    const traceDetail = await sessionApi.getSessionTraceDetail(sessionId, traceId)
    setDetail(traceDetail)
    setSelectedSpan(traceDetail.spans[0] ?? null)
  }

  return (
    <aside className="h-full w-[640px] bg-white border-l border-gray-200 flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Activity size={16} />
          <span>Trace</span>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose}>
          <X size={16} />
        </Button>
      </div>

      <div className="grid grid-cols-4 gap-2 p-3 border-b text-xs">
        <div className="rounded border p-2">
          <div className="text-gray-500">错误率</div>
          <div className="font-medium">{metrics ? `${Math.round(metrics.error_rate * 100)}%` : '-'}</div>
        </div>
        <div className="rounded border p-2">
          <div className="text-gray-500">平均耗时</div>
          <div className="font-medium">{metrics ? formatMs(metrics.avg_duration_ms) : '-'}</div>
        </div>
        <div className="rounded border p-2">
          <div className="text-gray-500">Token</div>
          <div className="font-medium">{metrics?.total_tokens ?? '-'}</div>
        </div>
        <div className="rounded border p-2">
          <div className="text-gray-500">模型</div>
          <div className="font-medium truncate">{metrics?.models.join(', ') || '-'}</div>
        </div>
      </div>

      {loading ? (
        <div className="p-4 text-sm text-gray-500">加载中...</div>
      ) : (
        <div className="grid grid-cols-[220px_1fr] min-h-0 flex-1">
          <ScrollArea className="border-r">
            <div className="p-2 flex flex-col gap-2">
              {traces.map((trace) => (
                <button
                  key={trace.trace_id}
                  type="button"
                  onClick={() => loadTrace(trace.trace_id)}
                  className="text-left rounded border p-2 text-xs hover:bg-gray-50"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium truncate">{trace.root_input_preview || trace.trace_id}</span>
                    {trace.status === 'error' ? <AlertCircle size={13} className="text-red-500" /> : <Clock size={13} />}
                  </div>
                  <div className="mt-1 text-gray-500">
                    {formatMs(trace.duration_ms)} · {trace.error_count} errors · {trace.total_tokens} tokens
                  </div>
                </button>
              ))}
            </div>
          </ScrollArea>

          <div className="grid grid-rows-[minmax(160px,240px)_1fr] min-h-0">
            <ScrollArea className="border-b">
              <div className="p-3">
                {detail ? (
                  <SpanTree spans={detail.spans} selectedId={selectedSpan?.id} onSelect={setSelectedSpan} />
                ) : (
                  <div className="text-sm text-gray-500">暂无 Trace</div>
                )}
              </div>
            </ScrollArea>
            <ScrollArea>
              <div className="p-3">
                {selectedSpan ? (
                  <div className="space-y-3 text-xs">
                    <div className="flex items-center gap-2 font-medium">
                      <Database size={14} />
                      <span>{selectedSpan.span_type} · {selectedSpan.name}</span>
                    </div>
                    <pre className="rounded bg-gray-950 text-gray-100 p-3 overflow-x-auto">
{formatJson({
  status: selectedSpan.status,
  duration_ms: selectedSpan.duration_ms,
  input: selectedSpan.input,
  output: selectedSpan.output,
  error: selectedSpan.error,
  attributes: selectedSpan.attributes,
})}
                    </pre>
                  </div>
                ) : (
                  <div className="text-sm text-gray-500">选择一个 span 查看详情</div>
                )}
              </div>
            </ScrollArea>
          </div>
        </div>
      )}
    </aside>
  )
}
```

- [ ] **Step 4: Add trace trigger to session header**

Modify `ui/src/components/session-header.tsx` import:

```tsx
import { Activity, Download, FileSearchCorner, FileText } from 'lucide-react'
```

Extend props:

```tsx
  onTraceOpen?: () => void
```

Add `onTraceOpen` to the destructured props.

Before the file dialog trigger, add:

```tsx
      {onTraceOpen && (
        <Button
          variant="ghost"
          size="icon-sm"
          className="cursor-pointer flex-shrink-0"
          onClick={onTraceOpen}
          aria-label="打开 Trace"
        >
          <Activity />
        </Button>
      )}
```

- [ ] **Step 5: Show TracePanel in session detail**

Modify `ui/src/components/session-detail-view.tsx` imports:

```tsx
import { TracePanel } from '@/components/trace-panel'
```

Add state:

```tsx
  const [traceOpen, setTraceOpen] = useState(false)
```

Pass trigger to `SessionHeader`:

```tsx
                onTraceOpen={() => setTraceOpen(true)}
```

After the tool preview panel block, add:

```tsx
        {traceOpen && (
          <div className="flex-shrink-0 h-full animate-in slide-in-from-right duration-300">
            <TracePanel sessionId={sessionId} onClose={() => setTraceOpen(false)} />
          </div>
        )}
```

- [ ] **Step 6: Run frontend checks**

Run:

```bash
cd ui && npm run lint
cd ui && npm run build
```

Expected: both commands pass.

- [ ] **Step 7: Commit Task 6**

```bash
git add ui/src/lib/api/types.ts \
  ui/src/lib/api/session.ts \
  ui/src/components/trace-panel.tsx \
  ui/src/components/session-header.tsx \
  ui/src/components/session-detail-view.tsx
git commit -m "feat: add trace panel"
```

---

### Task 7: End-to-End Verification and Cleanup

**Files:**
- Verify all files changed in Tasks 1-6.
- No new source file is expected in this task.

- [ ] **Step 1: Run backend targeted tests**

Run:

```bash
cd api && pytest tests/app/domain/models/test_trace.py \
  tests/app/infrastructure/models/test_trace_model.py \
  tests/app/domain/services/tracing/test_recorder.py \
  tests/app/application/services/test_trace_service.py \
  tests/app/interfaces/endpoints/test_trace_routes.py \
  tests/app/domain/services/test_agent_task_runner_tracing.py -v
```

Expected: PASS.

- [ ] **Step 2: Run existing backend regression tests likely affected by agent changes**

Run:

```bash
cd api && pytest tests/app/domain/services/test_agent_task_runner_skills.py \
  tests/app/domain/services/flows/test_planner_react_skills.py \
  tests/app/domain/services/flows/test_team_skills.py \
  tests/app/domain/services/agents/test_skill_context.py \
  tests/app/domain/services/tools/test_skill_tool.py -v
```

Expected: PASS.

- [ ] **Step 3: Run frontend checks**

Run:

```bash
cd ui && npm run lint
cd ui && npm run build
```

Expected: PASS.

- [ ] **Step 4: Inspect database migration order**

Run:

```bash
cd api && alembic heads
cd api && alembic history --verbose | head -60
```

Expected: one head; trace migration appears after the existing sessions/files/skills migrations.

- [ ] **Step 5: Review git diff for scope**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only trace implementation files are modified. `docs/agent-skills-implementation.md` remains untracked unless the user separately asks to handle it.

- [ ] **Step 6: Commit verification fixes if any were needed**

If Step 1-5 required changes, commit only those trace-related fixes:

```bash
git add api ui docs/superpowers/plans/2026-07-12-agent-trace-minimal.md
git commit -m "fix: stabilize trace implementation"
```

If no changes were needed, do not create an empty commit.

---

## Plan Self-Review

Spec coverage:

- Single `trace_spans` table: covered in Tasks 1-2.
- Best-effort `TraceRecorder`: covered in Task 3.
- Root, flow, LLM, tool, event spans: covered in Task 4.
- Model/token usage: covered by OpenAI usage attachment and LLM span attributes in Task 4.
- Read-only APIs for trace list, detail, metrics: covered in Task 5.
- Minimal UI trace panel: covered in Task 6.
- Redaction and truncation: covered in Task 3.
- Tests and verification: covered in Tasks 1-7.
- Non-goals respected: no eval, replay, exporter, checkpoint, `.env` change, or `trace_runs` table appears in implementation tasks.

Placeholder scan:

- The plan contains no open requirement placeholders.
- The Alembic revision id is fixed as `b7c9d8e2f1a3`, so migration creation does not depend on an interactive generator.

Type consistency:

- Backend uses `TraceSpanType`, `TraceSpanStatus`, `TraceSpan`, `TraceSummary`, and `TraceMetrics` consistently across model, repository, service, schema, and tests.
- Frontend status/type unions match backend enum values.
