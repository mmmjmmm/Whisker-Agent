import asyncio

import pytest

from app.application.errors.exceptions import ConflictError
from app.domain.models.team import AgentMode
from app.interfaces.endpoints.session_routes import chat
from app.interfaces.schemas.session import ChatRequest


class RejectingAgentService:
    def __init__(self):
        self.validated = []

    async def validate_chat_request(self, session_id, mode, has_message):
        self.validated.append((session_id, mode, has_message))
        raise ConflictError("Team 运行中不接受新消息")

    async def chat(self, **kwargs):
        raise AssertionError("SSE generator must not start after a conflict")
        yield


def test_chat_route_preflights_conflict_before_sse_response():
    async def scenario():
        service = RejectingAgentService()
        with pytest.raises(ConflictError):
            await chat(
                "session",
                ChatRequest(message="new", mode=AgentMode.TEAM),
                service,
            )
        assert service.validated == [("session", AgentMode.TEAM, True)]

    asyncio.run(scenario())
