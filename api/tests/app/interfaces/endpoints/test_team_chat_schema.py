import asyncio

import pytest

from app.application.errors.exceptions import ConflictError
from app.domain.models.team import AgentMode
from app.interfaces.endpoints.session_routes import chat
from app.interfaces.schemas.session import ChatRequest


class RejectingAgentService:
    def __init__(self):
        self.prepared = []

    async def prepare_chat(
        self,
        session_id,
        message,
        attachments,
        mode,
        timestamp=None,
    ):
        self.prepared.append(
            (session_id, message, attachments, mode, timestamp)
        )
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
        assert service.prepared == [
            ("session", "new", [], AgentMode.TEAM, None)
        ]

    asyncio.run(scenario())
