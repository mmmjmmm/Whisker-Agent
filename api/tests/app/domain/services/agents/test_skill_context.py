import asyncio
import copy
import json

from app.domain.models.app_config import AgentConfig
from app.domain.models.memory import Memory
from app.domain.services.agents.base import BaseAgent
from app.domain.services.skills.runtime import LoadedSkill
from app.domain.services.tools.skill import SkillTool


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FakeJSONParser:
    async def invoke(self, text: str, default_value=None):
        return json.loads(text)


class FakeSkillRuntime:
    async def load(self, name: str) -> LoadedSkill:
        return LoadedSkill(
            name=name,
            skill_md="FULL SKILL BODY",
            skill_dir="/skills/demo",
        )


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def invoke(
        self,
        messages,
        tools=None,
        response_format=None,
        tool_choice=None,
    ):
        self.calls.append({
            "messages": copy.deepcopy(messages),
            "tools": copy.deepcopy(tools),
            "tool_choice": tool_choice,
        })
        if len(self.calls) == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "load_skill",
                            "arguments": json.dumps({"name": "demo"}),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "done"}


class ProbeAgent(BaseAgent):
    name = "probe"
    _system_prompt = "BASE SYSTEM PROMPT"


class ProbePlanner(ProbeAgent):
    _tool_choice = "none"


class StoredMemorySessionRepository:
    def __init__(self, memory: Memory) -> None:
        self.memory = memory

    async def get_memory(self, session_id: str, agent_name: str) -> Memory:
        return self.memory

    async def save_memory(self, session_id: str, agent_name: str, memory: Memory) -> None:
        self.memory = memory


class StoredMemoryUnitOfWork:
    def __init__(self, memory: Memory) -> None:
        self.session = StoredMemorySessionRepository(memory)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FinalLLM:
    def __init__(self) -> None:
        self.messages = None

    async def invoke(self, messages, tools=None, response_format=None, tool_choice=None):
        self.messages = copy.deepcopy(messages)
        return {"role": "assistant", "content": "done"}


def build_agent(agent_type=ProbeAgent, *, tools=None, suffix=""):
    llm = FakeLLM()
    memory = Memory()
    agent = agent_type(
        uow_factory=FakeUnitOfWork,
        session_id="session-id",
        agent_config=AgentConfig(max_iterations=3, max_retries=2),
        llm=llm,
        json_parser=FakeJSONParser(),
        tools=tools or [],
        memory=memory,
        system_prompt_suffix=suffix,
    )
    return agent, llm, memory


def test_agent_catalog_is_lightweight_and_load_result_enters_memory() -> None:
    async def scenario() -> None:
        catalog = (
            "<available_skills><skill><name>demo</name>"
            "<description>demo description</description></skill>"
            "</available_skills>"
        )
        agent, llm, memory = build_agent(
            tools=[SkillTool(FakeSkillRuntime())],
            suffix=catalog,
        )

        events = [event async for event in agent.invoke("use demo")]

        first_system = llm.calls[0]["messages"][0]["content"]
        assert "BASE SYSTEM PROMPT" in first_system
        assert "demo description" in first_system
        assert "FULL SKILL BODY" not in first_system
        assert llm.calls[0]["tools"][0]["function"]["name"] == "load_skill"
        assert "FULL SKILL BODY" in json.dumps(
            llm.calls[1]["messages"],
            ensure_ascii=False,
        )
        assert len(events) == 3

        await agent.compact_memory()
        assert "FULL SKILL BODY" in memory.model_dump_json()

    asyncio.run(scenario())


def test_none_tool_choice_only_opens_when_skill_tool_exists() -> None:
    with_skill, _, _ = build_agent(
        ProbePlanner,
        tools=[SkillTool(FakeSkillRuntime())],
        suffix="catalog",
    )
    without_skill, _, _ = build_agent(ProbePlanner)

    assert with_skill._tool_choice is None
    assert without_skill._tool_choice == "none"


def test_new_agent_refreshes_catalog_in_persisted_memory() -> None:
    async def scenario() -> None:
        memory = Memory(messages=[
            {"role": "system", "content": "OLD CATALOG"},
            {"role": "user", "content": "history"},
        ])
        llm = FinalLLM()
        agent = ProbeAgent(
            uow_factory=lambda: StoredMemoryUnitOfWork(memory),
            session_id="session-id",
            agent_config=AgentConfig(max_iterations=3, max_retries=2),
            llm=llm,
            json_parser=FakeJSONParser(),
            tools=[],
            system_prompt_suffix="NEW CATALOG",
        )

        _ = [event async for event in agent.invoke("continue")]

        assert llm.messages[0]["content"] == (
            "BASE SYSTEM PROMPT\n\nNEW CATALOG"
        )
        assert llm.messages[1] == {"role": "user", "content": "history"}

    asyncio.run(scenario())
