from app.domain.models.app_config import AgentConfig
from app.domain.models.memory import Memory
from app.domain.services.agents.base import BaseAgent


class LegacyAgent(BaseAgent):
    name = "legacy"
    _system_prompt = "system"


class FakeSessionRepository:
    def __init__(self) -> None:
        self.loads: list[tuple[str, str]] = []
        self.saves: list[tuple[str, str, Memory]] = []

    async def get_memory(self, session_id: str, memory_key: str) -> Memory:
        self.loads.append((session_id, memory_key))
        return Memory(messages=[])

    async def save_memory(
            self,
            session_id: str,
            memory_key: str,
            memory: Memory,
    ) -> None:
        self.saves.append((session_id, memory_key, memory.model_copy(deep=True)))


class FakeUow:
    def __init__(self, session: FakeSessionRepository) -> None:
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class FakeLLM:
    async def invoke(self, **_kwargs):
        return {"role": "assistant", "content": "ok"}


class FakeJSONParser:
    async def invoke(self, value):
        return value


async def test_base_agent_defaults_to_session_scoped_memory() -> None:
    session_repository = FakeSessionRepository()
    agent = LegacyAgent(
        uow_factory=lambda: FakeUow(session_repository),
        session_id="session-1",
        agent_config=AgentConfig(),
        llm=FakeLLM(),
        json_parser=FakeJSONParser(),
        tools=[],
    )

    await agent._add_to_memory([{"role": "user", "content": "hello"}])

    assert session_repository.loads == [("session-1", "legacy")]
    assert [(session_id, key) for session_id, key, _ in session_repository.saves] == [
        ("session-1", "legacy")
    ]
    assert session_repository.saves[0][2].messages[0]["role"] == "system"


async def test_base_agent_uses_explicit_attempt_memory_key() -> None:
    from app.domain.services.research.memory_store import EphemeralMemoryStore

    store = EphemeralMemoryStore()
    agent = LegacyAgent(
        uow_factory=lambda: FakeUow(FakeSessionRepository()),
        session_id="session-1",
        agent_config=AgentConfig(),
        llm=FakeLLM(),
        json_parser=FakeJSONParser(),
        tools=[],
        memory_store=store,
        memory_key="run-1/task-1/attempt-1",
    )

    await agent._add_to_memory([{"role": "user", "content": "hello"}])

    memory = await store.load("run-1/task-1/attempt-1")
    assert [message["role"] for message in memory.messages] == ["system", "user"]
