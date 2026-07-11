import asyncio

import pytest

from app.domain.models.app_config import AgentConfig
from app.domain.models.memory import Memory
from app.domain.services.agents.base import BaseAgent
from app.domain.services.tools.base import BaseTool, tool


class FakeSessionRepository:
    def __init__(self):
        self.save_memory_calls: list[tuple[str, str, Memory]] = []

    async def get_memory(self, session_id, agent_name):
        return Memory()

    async def save_memory(self, session_id, agent_name, memory):
        self.save_memory_calls.append((session_id, agent_name, memory.model_copy(deep=True)))


class FakeUow:
    def __init__(self):
        self.session = FakeSessionRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


class DemoAgentTool(BaseTool):
    name = "demo"

    @tool(name="search_web", description="search", parameters={}, required=[])
    async def search_web(self):
        raise AssertionError("not invoked")

    @tool(name="shell_execute", description="shell", parameters={}, required=[])
    async def shell_execute(self):
        raise AssertionError("not invoked")


class DummyAgent(BaseAgent):
    name = "test_agent"
    _system_prompt = "system"


def make_agent(**overrides):
    uow = FakeUow()
    kwargs = {
        "uow_factory": lambda: uow,
        "session_id": "session-1",
        "agent_config": AgentConfig(),
        "llm": object(),
        "json_parser": object(),
        "tools": [DemoAgentTool()],
    }
    kwargs.update(overrides)
    return DummyAgent(**kwargs), uow


def test_agent_filters_schema_and_rejects_runtime_bypass():
    agent, uow = make_agent(
        allowed_tool_names={"search_web"},
        memory=Memory(),
        persist_memory=False,
    )

    assert [
        item["function"]["name"] for item in agent._get_available_tools()
    ] == ["search_web"]
    assert agent._get_tool("search_web").name == "demo"
    with pytest.raises(PermissionError, match="未授权"):
        agent._get_tool("shell_execute")

    asyncio.run(agent._add_to_memory([{"role": "user", "content": "isolated"}]))

    assert agent._memory.get_last_message()["content"] == "isolated"
    assert uow.session.save_memory_calls == []


def test_default_agent_behavior_still_persists_by_agent_name():
    agent, uow = make_agent()

    asyncio.run(agent._add_to_memory([{"role": "user", "content": "persisted"}]))

    assert len(uow.session.save_memory_calls) == 1
    session_id, memory_key, memory = uow.session.save_memory_calls[0]
    assert session_id == "session-1"
    assert memory_key == "test_agent"
    assert memory.get_last_message()["content"] == "persisted"
