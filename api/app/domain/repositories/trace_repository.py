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
