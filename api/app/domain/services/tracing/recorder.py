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
TOKEN_USAGE_KEYS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}
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
            if lower_key not in TOKEN_USAGE_KEYS and any(
                part in lower_key for part in SENSITIVE_KEY_PARTS
            ):
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

    preview = encoded.encode("utf-8")[:max_bytes].decode(
        "utf-8",
        errors="ignore",
    )
    return {
        "_truncated": True,
        "_original_size": size,
        "_preview": preview,
    }


def _error_payload(
    error: BaseException | dict[str, Any] | str | None,
) -> dict[str, Any] | None:
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
    """Best-effort Trace recorder.

    Recorder errors are intentionally swallowed so tracing cannot break agent
    execution.
    """

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
        parent = (
            parent_span_id
            if parent_span_id is not None
            else (stack[-1].id if stack else None)
        )
        resolved_trace_id = trace_id or (
            stack[-1].trace_id if stack else str(uuid.uuid4())
        )
        span = TraceSpan(
            trace_id=resolved_trace_id,
            session_id=self._session_id,
            parent_span_id=parent,
            span_type=span_type,
            name=name,
            input=redact_and_truncate(
                {} if input is None else input,
                max_bytes=self._max_payload_bytes,
            ),
            attributes=redact_and_truncate(
                attributes or {},
                max_bytes=self._max_payload_bytes,
            ),
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
            input=span.input,
            attributes=span.attributes,
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
        status = TraceSpanStatus.ERROR if error_data is not None else TraceSpanStatus.OK
        end_attributes = redact_and_truncate(
            attributes or {},
            max_bytes=self._max_payload_bytes,
        )
        merged_attributes = redact_and_truncate(
            {**span.attributes, **end_attributes},
            max_bytes=self._max_payload_bytes,
        )
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
            duration_ms=max(
                0,
                int((ended_at - span.started_at).total_seconds() * 1000),
            ),
            input=span.input,
            output=redact_and_truncate(
                {} if output is None else output,
                max_bytes=self._max_payload_bytes,
            ),
            error=error_data,
            attributes=merged_attributes,
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
                _span_stack.set(
                    tuple(item for item in stack if item.id != span.id)
                )
