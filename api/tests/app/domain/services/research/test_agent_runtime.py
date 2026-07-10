import json

import pytest
from pydantic import BaseModel

from app.domain.external.llm import LLMInvocationResult, LLMUsage
from app.domain.models.agent_run import CapabilityProfile, RunBudget
from app.domain.models.event import ResearchUsageEvent, ToolEvent, ToolEventStatus
from app.domain.models.tool_result import ToolResult
from app.domain.services.research.agent_runtime import (
    AgentRuntimeContext,
    TeamAgentRuntime,
)
from app.domain.services.research.budget import RunBudgetManager
from app.domain.services.research.memory_store import EphemeralMemoryStore
from app.domain.services.research.tool_policy import PolicyViolation, ToolPolicy
from app.domain.services.tools.base import BaseTool, tool
from tests.fakes.llm import FakeLLM


class Output(BaseModel):
    answer: str


class FakeJSONParser:
    async def invoke(self, text: str, default_value=None):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return default_value


class CountingTools(BaseTool):
    name = "counting"

    def __init__(self) -> None:
        super().__init__()
        self.shell_invoke_count = 0
        self.read_invoke_count = 0

    @tool(
        name="shell_exec",
        description="execute",
        parameters={"command": {"type": "string"}},
        required=["command"],
    )
    async def shell_exec(self, command: str) -> ToolResult:
        self.shell_invoke_count += 1
        return ToolResult(data={"command": command})

    @tool(
        name="web_read",
        description="read",
        parameters={"url": {"type": "string"}},
        required=["url"],
    )
    async def web_read(self, url: str) -> ToolResult:
        self.read_invoke_count += 1
        return ToolResult(data={"url": url, "text": "fact"})


def llm_result(message: dict, tokens: int = 10) -> LLMInvocationResult:
    return LLMInvocationResult(
        message=message,
        model="fake-model",
        usage=LLMUsage(
            input_tokens=tokens - 2,
            output_tokens=2,
            total_tokens=tokens,
        ),
    )


def runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        session_id="session-1",
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        agent_id="agent-1",
        agent_profile="worker",
    )


async def test_runtime_rejects_hidden_tool_before_invocation() -> None:
    tools = CountingTools()
    llm = FakeLLM([
        llm_result({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "function": {
                    "name": "shell_exec",
                    "arguments": '{"command":"pwd"}',
                },
            }],
        })
    ])
    runtime = TeamAgentRuntime(
        llm=llm,
        tool_policy=ToolPolicy([tools]),
        budget=RunBudgetManager(RunBudget()),
        memory_store=EphemeralMemoryStore(),
        json_parser=FakeJSONParser(),
        context=runtime_context(),
    )

    with pytest.raises(PolicyViolation, match="shell_exec"):
        await runtime.run(
            prompt="research",
            output_type=Output,
            profile=CapabilityProfile.RESEARCH_READONLY,
            memory_key="attempt-1",
        )

    assert tools.shell_invoke_count == 0


async def test_runtime_emits_correlated_tool_and_usage_events() -> None:
    tools = CountingTools()
    events = []
    llm = FakeLLM([
        llm_result({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "function": {
                    "name": "web_read",
                    "arguments": '{"url":"https://example.com"}',
                },
            }],
        }),
        llm_result({
            "role": "assistant",
            "content": '{"answer":"ok"}',
        }),
    ])

    async def emit(event) -> None:
        events.append(event)

    runtime = TeamAgentRuntime(
        llm=llm,
        tool_policy=ToolPolicy([tools]),
        budget=RunBudgetManager(RunBudget()),
        memory_store=EphemeralMemoryStore(),
        json_parser=FakeJSONParser(),
        context=runtime_context(),
        emit=emit,
    )

    output = await runtime.run(
        prompt="research",
        output_type=Output,
        profile=CapabilityProfile.RESEARCH_READONLY,
        memory_key="attempt-1",
    )

    assert output.answer == "ok"
    assert tools.read_invoke_count == 1
    tool_events = [event for event in events if isinstance(event, ToolEvent)]
    assert [event.status for event in tool_events] == [
        ToolEventStatus.CALLING,
        ToolEventStatus.CALLED,
    ]
    assert all(event.run_id == "run-1" for event in tool_events)
    assert all(event.task_id == "task-1" for event in tool_events)
    assert all(event.attempt_id == "attempt-1" for event in tool_events)
    assert all(event.agent_id == "agent-1" for event in tool_events)
    assert len([event for event in events if isinstance(event, ResearchUsageEvent)]) == 2


async def test_runtime_repairs_invalid_structured_output_once() -> None:
    llm = FakeLLM([
        llm_result({"role": "assistant", "content": "not-json"}),
        llm_result({"role": "assistant", "content": '{"answer":"fixed"}'}),
    ])
    runtime = TeamAgentRuntime(
        llm=llm,
        tool_policy=ToolPolicy([]),
        budget=RunBudgetManager(RunBudget()),
        memory_store=EphemeralMemoryStore(),
        json_parser=FakeJSONParser(),
        context=runtime_context(),
    )

    output = await runtime.run(
        prompt="analyze",
        output_type=Output,
        profile=CapabilityProfile.ANALYSIS,
        memory_key="attempt-1",
    )

    assert output.answer == "fixed"
    assert len(llm.calls) == 2
