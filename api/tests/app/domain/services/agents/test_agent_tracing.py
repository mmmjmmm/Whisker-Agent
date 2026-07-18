import asyncio
import json
from dataclasses import dataclass, field

import pytest

from app.domain.models.app_config import AgentConfig
from app.domain.models.event import WaitEvent
from app.domain.models.memory import Memory
from app.domain.models.message import Message
from app.domain.models.plan import Plan, Step
from app.domain.models.team import (
    TaskGraph,
    TeamCapability,
    TeamTask,
    TeamTaskStatus,
    WorkerResult,
)
from app.domain.models.tool_result import ToolResult
from app.domain.models.trace import TraceSpan, TraceSpanStatus, TraceSpanType
from app.domain.services.agents.base import BaseAgent
from app.domain.services.agents.planner import PlannerAgent
from app.domain.services.agents.react import ReActAgent
from app.domain.services.agents.task_worker import TaskWorker
from app.domain.services.agents.team_planner import TeamPlannerAgent
from app.domain.services.agents.team_synthesizer import TeamSynthesizerAgent
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.message import MessageTool
from app.domain.services.team.graph import TaskGraphError
from app.domain.services.tracing import TraceRecorder


@dataclass
class FakeTraceRepository:
    spans: dict[str, TraceSpan] = field(default_factory=dict)

    async def create_span(self, span: TraceSpan) -> None:
        self.spans[span.id] = span

    async def finish_span(self, span: TraceSpan) -> None:
        self.spans[span.id] = span


class FakeUnitOfWork:
    def __init__(self, repository: FakeTraceRepository) -> None:
        self.trace = repository

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FakeJSONParser:
    async def invoke(self, text: str, default_value=None):
        return json.loads(text)


class SequencedLLM:
    model_name = "trace-model"
    temperature = 0.2
    max_tokens = 1024

    def __init__(self, responses: list[dict | BaseException]) -> None:
        self._responses = responses
        self.calls = 0

    async def invoke(
        self,
        messages,
        tools=None,
        response_format=None,
        tool_choice=None,
    ):
        response = self._responses[self.calls]
        self.calls += 1
        if isinstance(response, BaseException):
            raise response
        return response


class BlockingLLM:
    model_name = "trace-model"
    temperature = 0.2
    max_tokens = 1024

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def invoke(
        self,
        messages,
        tools=None,
        response_format=None,
        tool_choice=None,
    ):
        self.entered.set()
        await asyncio.Future()


class ProbeAgent(BaseAgent):
    name = "probe"
    _system_prompt = "probe"


class UnstableTool(BaseTool):
    name = "unstable"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    @tool(
        name="unstable_call",
        description="unstable call",
        parameters={"value": {"type": "string"}},
        required=["value"],
    )
    async def unstable_call(self, value: str) -> ToolResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary tool failure")
        return ToolResult(success=True, data={"value": value})


class BlockingTool(BaseTool):
    name = "blocking"

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    @tool(
        name="blocking_call",
        description="blocking call",
        parameters={"value": {"type": "string"}},
        required=["value"],
    )
    async def blocking_call(self, value: str) -> ToolResult:
        self.entered.set()
        await asyncio.Future()


def plan_response() -> dict:
    return {
        "role": "assistant",
        "content": json.dumps(
            {
                "id": "plan-1",
                "title": "Trace Plan",
                "goal": "trace this",
                "language": "zh",
                "steps": [
                    {
                        "id": "step-1",
                        "description": "execute",
                    }
                ],
                "message": "planned",
            }
        ),
        "_usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


def build_agent(
    agent_type,
    repository: FakeTraceRepository,
    llm: SequencedLLM,
    *,
    tools: list[BaseTool] | None = None,
):
    recorder = TraceRecorder(
        lambda: FakeUnitOfWork(repository),
        session_id="session-1",
    )
    agent = agent_type(
        uow_factory=lambda: FakeUnitOfWork(repository),
        session_id="session-1",
        agent_config=AgentConfig(max_iterations=3, max_retries=2),
        llm=llm,
        json_parser=FakeJSONParser(),
        tools=tools or [],
        memory=Memory(),
        trace_recorder=recorder,
    )
    agent._retry_interval = 0
    return agent, recorder


def test_planner_operation_span_contains_llm_and_structured_result() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        planner, recorder = build_agent(
            PlannerAgent,
            repository,
            SequencedLLM([plan_response()]),
        )

        async with recorder.span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        ):
            async with recorder.span(
                span_type=TraceSpanType.FLOW,
                name="planner_react",
            ) as flow_scope:
                events = [
                    event
                    async for event in planner.create_plan(
                        Message(message="trace this")
                    )
                ]

        assert len(events) == 1
        spans = list(repository.spans.values())
        agent_span = next(
            span for span in spans
            if span.span_type is TraceSpanType.AGENT
        )
        llm_span = next(
            span for span in spans
            if span.span_type is TraceSpanType.LLM
        )
        assert agent_span.name == "planner.create_plan"
        assert agent_span.parent_span_id == flow_scope.handle.id
        assert agent_span.status is TraceSpanStatus.OK
        assert agent_span.attributes == {
            "agent_name": "planner",
            "operation": "create_plan",
            "attempt": 1,
            "max_attempts": 1,
        }
        assert agent_span.input == {
            "message": "trace this",
            "attachments": [],
        }
        assert agent_span.output["id"] == "plan-1"
        assert agent_span.output["steps"][0]["id"] == "step-1"
        assert llm_span.parent_span_id == agent_span.id

    asyncio.run(scenario())


def test_planner_operation_span_captures_result_validation_error() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        planner, recorder = build_agent(
            PlannerAgent,
            repository,
            SequencedLLM([
                {"role": "assistant", "content": "not-json"},
            ]),
        )

        with pytest.raises(json.JSONDecodeError):
            async with recorder.span(
                span_type=TraceSpanType.ROOT,
                name="chat",
                trace_id="trace-1",
            ):
                _ = [
                    event
                    async for event in planner.create_plan(
                        Message(message="trace this")
                    )
                ]

        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert agent_span.status is TraceSpanStatus.ERROR
        assert agent_span.error["type"] == "JSONDecodeError"

    asyncio.run(scenario())


def test_llm_retry_spans_record_attempt_metadata() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        planner, recorder = build_agent(
            PlannerAgent,
            repository,
            SequencedLLM([
                RuntimeError("temporary llm failure"),
                plan_response(),
            ]),
        )

        async with recorder.span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        ):
            _ = [
                event
                async for event in planner.create_plan(
                    Message(message="trace this")
                )
            ]

        llm_spans = sorted(
            (
                span for span in repository.spans.values()
                if span.span_type is TraceSpanType.LLM
            ),
            key=lambda span: span.started_at,
        )
        assert [span.status for span in llm_spans] == [
            TraceSpanStatus.ERROR,
            TraceSpanStatus.OK,
        ]
        assert [span.attributes["attempt"] for span in llm_spans] == [1, 2]
        assert all(span.attributes["max_attempts"] == 2 for span in llm_spans)

    asyncio.run(scenario())


def test_llm_span_is_cancelled_while_provider_call_is_running() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        llm = BlockingLLM()
        planner, recorder = build_agent(
            PlannerAgent,
            repository,
            llm,
        )

        async with recorder.span(
            span_type=TraceSpanType.FLOW,
            name="planner_react",
            trace_id="trace-1",
        ):
            async def execute() -> None:
                _ = [
                    event
                    async for event in planner.create_plan(
                        Message(message="trace this")
                    )
                ]

            task = asyncio.create_task(execute())
            await llm.entered.wait()
            token = recorder.set_cancellation_reason(
                "superseded_by_new_input"
            )
            try:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                recorder.reset_cancellation_reason(token)

        llm_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.LLM
        )
        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert llm_span.status is TraceSpanStatus.CANCELLED
        assert agent_span.status is TraceSpanStatus.CANCELLED
        assert llm_span.attributes["cancellation_reason"] == (
            "superseded_by_new_input"
        )
        assert agent_span.attributes["cancellation_reason"] == (
            "superseded_by_new_input"
        )

    asyncio.run(scenario())


def test_tool_retry_spans_record_attempt_and_tool_call_id() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        tool_package = UnstableTool()
        agent, recorder = build_agent(
            ProbeAgent,
            repository,
            SequencedLLM([
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "unstable_call",
                                "arguments": json.dumps({"value": "ok"}),
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "done"},
            ]),
            tools=[tool_package],
        )

        async with recorder.span(
            span_type=TraceSpanType.ROOT,
            name="chat",
            trace_id="trace-1",
        ):
            _ = [event async for event in agent.invoke("run tool")]

        tool_spans = sorted(
            (
                span for span in repository.spans.values()
                if span.span_type is TraceSpanType.TOOL
            ),
            key=lambda span: span.started_at,
        )
        assert [span.status for span in tool_spans] == [
            TraceSpanStatus.ERROR,
            TraceSpanStatus.OK,
        ]
        assert [span.attributes["attempt"] for span in tool_spans] == [1, 2]
        assert all(span.attributes["max_attempts"] == 2 for span in tool_spans)
        assert all(span.attributes["tool_call_id"] == "call-1" for span in tool_spans)

    asyncio.run(scenario())


def test_tool_span_is_cancelled_while_tool_call_is_running() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        blocking_tool = BlockingTool()
        react, recorder = build_agent(
            ReActAgent,
            repository,
            SequencedLLM([
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-blocking",
                            "function": {
                                "name": "blocking_call",
                                "arguments": json.dumps({"value": "wait"}),
                            },
                        }
                    ],
                },
            ]),
            tools=[blocking_tool],
        )
        plan = Plan(
            id="plan-1",
            goal="trace this",
            language="zh",
            steps=[Step(id="step-1", description="wait")],
        )

        async with recorder.span(
            span_type=TraceSpanType.TASK,
            name="plan.step",
            trace_id="trace-1",
        ):
            async def execute() -> None:
                _ = [
                    event
                    async for event in react.execute_step(
                        plan,
                        plan.steps[0],
                        Message(message="trace this"),
                    )
                ]

            task = asyncio.create_task(execute())
            await blocking_tool.entered.wait()
            token = recorder.set_cancellation_reason(
                "superseded_by_new_input"
            )
            try:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                recorder.reset_cancellation_reason(token)

        tool_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.TOOL
        )
        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert tool_span.status is TraceSpanStatus.CANCELLED
        assert agent_span.status is TraceSpanStatus.CANCELLED
        assert tool_span.attributes["cancellation_reason"] == (
            "superseded_by_new_input"
        )
        assert agent_span.attributes["cancellation_reason"] == (
            "superseded_by_new_input"
        )

    asyncio.run(scenario())


def test_react_execute_step_marks_agent_waiting() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        react, recorder = build_agent(
            ReActAgent,
            repository,
            SequencedLLM([
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-wait",
                            "function": {
                                "name": "message_ask_user",
                                "arguments": json.dumps({"text": "继续吗？"}),
                            },
                        }
                    ],
                },
            ]),
            tools=[MessageTool()],
        )
        plan = Plan(
            id="plan-1",
            goal="trace this",
            language="zh",
            steps=[Step(id="step-1", description="wait")],
        )
        step = plan.steps[0]

        async with recorder.span(
            span_type=TraceSpanType.TASK,
            name="plan.step",
            trace_id="trace-1",
        ) as task_scope:
            events = [
                event
                async for event in react.execute_step(
                    plan,
                    step,
                    Message(message="trace this"),
                )
            ]

        assert any(isinstance(event, WaitEvent) for event in events)
        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        tool_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.TOOL
        )
        assert agent_span.name == "react.execute_step"
        assert agent_span.parent_span_id == task_scope.handle.id
        assert agent_span.status is TraceSpanStatus.WAITING
        assert agent_span.attributes["plan_id"] == "plan-1"
        assert agent_span.attributes["step_id"] == "step-1"
        assert agent_span.output == {
            "step_id": "step-1",
            "status": "waiting",
        }
        assert tool_span.parent_span_id == agent_span.id

    asyncio.run(scenario())


def test_task_worker_operation_span_records_task_attempt_context() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        recorder = TraceRecorder(
            lambda: FakeUnitOfWork(repository),
            session_id="session-1",
        )
        task = TeamTask(
            id="task-1",
            description="analyze",
            capability=TeamCapability.ANALYSIS,
            success_criteria="done",
        )
        worker = TaskWorker(
            uow_factory=lambda: FakeUnitOfWork(repository),
            session_id="session-1",
            agent_config=AgentConfig(max_iterations=3, max_retries=2),
            llm=SequencedLLM([
                {
                    "role": "assistant",
                    "content": json.dumps({
                        "success": True,
                        "summary": "finished",
                    }),
                }
            ]),
            json_parser=FakeJSONParser(),
            tools=[],
            memory=Memory(),
            trace_recorder=recorder,
            graph_id="graph-1",
            task=task,
            agent_id="worker-1",
            attempt=2,
            max_attempts=3,
        )

        async def emit(event) -> None:
            raise AssertionError(f"unexpected event: {event}")

        async with recorder.span(
            span_type=TraceSpanType.TASK,
            name="team.task",
            trace_id="trace-1",
        ) as task_scope:
            result = await worker.execute(
                goal="trace this",
                dependency_results={},
                attachments=[],
                emit=emit,
            )

        assert result.success is True
        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert agent_span.name == "task_worker.execute"
        assert agent_span.parent_span_id == task_scope.handle.id
        assert agent_span.attributes == {
            "agent_name": "task_worker",
            "operation": "execute",
            "attempt": 2,
            "max_attempts": 3,
            "graph_id": "graph-1",
            "task_id": "task-1",
            "agent_id": "worker-1",
            "capability": "analysis",
        }
        assert agent_span.output["summary"] == "finished"

    asyncio.run(scenario())


def team_plan_response(*, dependency: str | None = None) -> dict:
    return {
        "role": "assistant",
        "content": json.dumps({
            "title": "Team Trace",
            "goal": "trace this",
            "tasks": [
                {
                    "id": "task-1",
                    "description": "analyze",
                    "dependencies": [dependency] if dependency else [],
                    "capability": "analysis",
                    "success_criteria": "done",
                }
            ],
        }),
    }


def test_team_planner_span_includes_dag_validation() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        recorder = TraceRecorder(
            lambda: FakeUnitOfWork(repository),
            session_id="session-1",
        )
        planner = TeamPlannerAgent(
            uow_factory=lambda: FakeUnitOfWork(repository),
            session_id="session-1",
            agent_config=AgentConfig(max_iterations=3, max_retries=2),
            llm=SequencedLLM([team_plan_response()]),
            json_parser=FakeJSONParser(),
            tools=[],
            memory=Memory(),
            trace_recorder=recorder,
        )

        async with recorder.span(
            span_type=TraceSpanType.FLOW,
            name="team",
            trace_id="trace-1",
        ) as flow_scope:
            graph = await planner.create_graph(
                Message(message="trace this"),
                attempt=2,
                max_attempts=2,
            )

        assert isinstance(graph, TaskGraph)
        assert graph.tasks[0].id == "task-1"
        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert agent_span.name == "team_planner.create_graph"
        assert agent_span.parent_span_id == flow_scope.handle.id
        assert agent_span.attributes["attempt"] == 2
        assert agent_span.attributes["max_attempts"] == 2
        assert agent_span.output["tasks"][0]["id"] == "task-1"

    asyncio.run(scenario())


def test_team_planner_span_marks_invalid_dag_as_error() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        recorder = TraceRecorder(
            lambda: FakeUnitOfWork(repository),
            session_id="session-1",
        )
        planner = TeamPlannerAgent(
            uow_factory=lambda: FakeUnitOfWork(repository),
            session_id="session-1",
            agent_config=AgentConfig(max_iterations=3, max_retries=2),
            llm=SequencedLLM([
                team_plan_response(dependency="missing-task"),
            ]),
            json_parser=FakeJSONParser(),
            tools=[],
            memory=Memory(),
            trace_recorder=recorder,
        )

        with pytest.raises(ValueError):
            await planner.create_graph(Message(message="trace this"))

        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert agent_span.status is TraceSpanStatus.ERROR
        assert agent_span.error["type"] == TaskGraphError.__name__

    asyncio.run(scenario())


def test_team_synthesizer_operation_span_records_attempt_and_result() -> None:
    async def scenario() -> None:
        repository = FakeTraceRepository()
        recorder = TraceRecorder(
            lambda: FakeUnitOfWork(repository),
            session_id="session-1",
        )
        graph = TaskGraph(
            id="graph-1",
            title="Team Trace",
            goal="trace this",
            tasks=[
                TeamTask(
                    id="task-1",
                    description="analyze",
                    capability=TeamCapability.ANALYSIS,
                    success_criteria="done",
                    status=TeamTaskStatus.COMPLETED,
                    result=WorkerResult(success=True, summary="finished"),
                )
            ],
        )
        synthesizer, recorder = build_agent(
            TeamSynthesizerAgent,
            repository,
            SequencedLLM([
                {
                    "role": "assistant",
                    "content": json.dumps({
                        "message": "final answer",
                        "attachments": [],
                    }),
                }
            ]),
        )

        async with recorder.span(
            span_type=TraceSpanType.FLOW,
            name="team",
            trace_id="trace-1",
        ) as flow_scope:
            result = await synthesizer.synthesize(
                graph,
                attempt=2,
                max_attempts=2,
            )

        assert result.message == "final answer"
        agent_span = next(
            span for span in repository.spans.values()
            if span.span_type is TraceSpanType.AGENT
        )
        assert agent_span.name == "team_synthesizer.synthesize"
        assert agent_span.parent_span_id == flow_scope.handle.id
        assert agent_span.attributes["graph_id"] == "graph-1"
        assert agent_span.attributes["attempt"] == 2
        assert agent_span.attributes["max_attempts"] == 2
        assert agent_span.output == {
            "message": "final answer",
            "attachments": [],
        }

    asyncio.run(scenario())
