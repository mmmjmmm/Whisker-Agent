from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.errors.exceptions import RunAlreadyActiveError
from app.domain.models.agent_run import AgentMode
from app.domain.models.event import DoneEvent, ErrorEvent, MessageEvent
from app.interfaces.endpoints.session_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_agent_service


class ConflictingService:
    async def prepare_chat(self, *_args, **_kwargs):
        raise RunAlreadyActiveError("run-1", "running")

    async def chat(self, *_args, **_kwargs):
        yield ErrorEvent(error="conflict was incorrectly streamed")


class StreamingService:
    def __init__(self) -> None:
        self.mode = None
        self.prepared = object()

    async def prepare_chat(self, *_args, **kwargs):
        self.mode = kwargs["mode"]
        return self.prepared

    async def stream_prepared_chat(self, prepared):
        assert prepared is self.prepared
        yield MessageEvent(role="assistant", message="result")
        yield DoneEvent()


def test_chat_conflict_is_http_409_not_sse_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_agent_service] = ConflictingService

    with TestClient(app) as client:
        response = client.post(
            "/api/sessions/session-1/chat",
            json={"message": "new", "mode": "react"},
        )

    assert response.status_code == 409
    assert response.json()["data"]["error_code"] == "RUN_ALREADY_ACTIVE"


def test_chat_streams_prepared_events_and_forwards_mode() -> None:
    service = StreamingService()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_agent_service] = lambda: service

    with TestClient(app) as client:
        response = client.post(
            "/api/sessions/session-1/chat",
            json={"message": "research", "mode": "research_team"},
        )

    assert response.status_code == 200
    assert service.mode == AgentMode.RESEARCH_TEAM
    assert "event: message" in response.text
    assert "event: done" in response.text
    assert "result" in response.text
