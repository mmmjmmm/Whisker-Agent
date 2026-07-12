from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models.trace import (
    TraceMetrics,
    TraceSpan,
    TraceSpanStatus,
    TraceSpanType,
    TraceSummary,
)
from app.interfaces.endpoints import session_routes
from app.interfaces.service_dependencies import get_trace_service


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


def make_client(service: FakeTraceService) -> TestClient:
    app = FastAPI()
    app.include_router(session_routes.router, prefix="/api")
    app.dependency_overrides[get_trace_service] = lambda: service
    return TestClient(app)


def test_trace_routes() -> None:
    client = make_client(FakeTraceService())

    traces = client.get("/api/sessions/session-1/traces")
    detail = client.get("/api/sessions/session-1/traces/trace-1")
    metrics = client.get("/api/sessions/session-1/trace-metrics")

    assert traces.status_code == 200
    assert traces.json()["data"]["traces"][0]["trace_id"] == "trace-1"
    assert detail.status_code == 200
    assert detail.json()["data"]["spans"][0]["id"] == "span-1"
    assert metrics.status_code == 200
    assert metrics.json()["data"]["trace_count"] == 1
