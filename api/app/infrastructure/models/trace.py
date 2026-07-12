from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.trace import TraceSpan
from app.infrastructure.models.base import Base


class TraceSpanModel(Base):
    """Trace Span ORM 模型。"""

    __tablename__ = "trace_spans"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    span_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
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
        return cls(**span.model_dump(mode="python"))

    def to_domain(self) -> TraceSpan:
        return TraceSpan.model_validate(self, from_attributes=True)

    def update_from_domain(self, span: TraceSpan) -> None:
        for field, value in span.model_dump(mode="python").items():
            setattr(self, field, value)
