import asyncio

from app.domain.models.app_config import AgentConfig
from app.domain.models.event import MessageDeltaEvent, MessageEvent
from app.domain.models.memory import Memory
from app.domain.services.agents.react import ReActAgent


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FakeJSONParser:
    async def invoke(self, text: str, default_value=None):
        return {}


class StreamChunk:
    def __init__(self, content: str, usage: dict | None = None) -> None:
        self.content = content
        self.usage = usage


class StreamingLLM:
    model_name = "stream-model"
    temperature = 0.2
    max_tokens = 1024

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self.chunks = chunks
        self.calls = []

    async def invoke(self, messages, tools=None, response_format=None, tool_choice=None):
        raise AssertionError("summarize_stream must use the streaming LLM path")

    async def stream(self, messages, tools=None, response_format=None, tool_choice=None):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "response_format": response_format,
                "tool_choice": tool_choice,
            }
        )
        for chunk in self.chunks:
            yield chunk


def test_react_summary_streams_deltas_then_final_message() -> None:
    async def scenario() -> None:
        llm = StreamingLLM([
            StreamChunk("任务"),
            StreamChunk("完成", {"total_tokens": 12}),
        ])
        agent = ReActAgent(
            uow_factory=FakeUnitOfWork,
            session_id="session-1",
            agent_config=AgentConfig(max_retries=2),
            llm=llm,
            json_parser=FakeJSONParser(),
            tools=[],
            memory=Memory(),
        )

        events = [
            event
            async for event in agent.summarize_stream(
                attachments=["/home/ubuntu/report.md"],
            )
        ]

        deltas = [
            event for event in events
            if isinstance(event, MessageDeltaEvent)
        ]
        final = events[-1]

        assert [event.delta for event in deltas] == ["任务", "完成"]
        assert isinstance(final, MessageEvent)
        assert final.message == "任务完成"
        assert final.stream_id == deltas[0].stream_id
        assert final.attachments[0].filepath == "/home/ubuntu/report.md"
        assert llm.calls[0]["tools"] == []
        assert llm.calls[0]["response_format"] is None

    asyncio.run(scenario())
