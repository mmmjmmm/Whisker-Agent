from app.domain.external.llm import LLMInvocationResult, LLMUsage
from app.domain.models.agent_run import (
    AgentMode,
    AgentRun,
    AgentTask,
    AttemptStatus,
    CapabilityProfile,
    TaskAttempt,
)
from app.domain.models.research import FindingBundle, WorkerContext
from app.domain.services.agents.coverage_reviewer import CoverageReviewerAgent
from app.domain.services.agents.research_planner import ResearchPlannerAgent
from app.domain.services.agents.research_synthesizer import ResearchSynthesizerAgent
from app.domain.services.flows.research_orchestrator import ResearchOrchestrator
from app.domain.services.flows.research_team import ResearchTeamFlow
from app.domain.services.flows.research_team_factory import ResearchTeamFlowFactory
from app.domain.services.research.citation_verifier import CitationVerifier
from app.domain.services.research.event_sequencer import EventSequencer
from app.domain.services.research.memory_store import EphemeralMemoryStore


class FakeLLM:
    max_tokens = 100
    model_name = "fake"

    def __init__(self) -> None:
        self.calls = []

    async def invoke_with_usage(self, **kwargs):
        self.calls.append(kwargs)
        bundle = FindingBundle(task_id="task-1", summary="done")
        return LLMInvocationResult(
            message={"role": "assistant", "content": bundle.model_dump_json()},
            model="fake",
            usage=LLMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


class FakeJSONParser:
    async def invoke(self, value, default_value=None):
        return default_value


class FakeWebReader:
    async def read(self, url):
        raise AssertionError(f"unexpected web read: {url}")


class FakeSourceStorage:
    async def put(self, **_kwargs):
        return "object-key"

    async def get(self, _object_key):
        return b"content"


class FakeFileStorage:
    pass


class FakeSearchEngine:
    async def invoke(self, *_args, **_kwargs):
        raise AssertionError("unexpected search")


def build_factory(llm):
    return ResearchTeamFlowFactory(
        uow_factory=lambda: object(),
        llm=llm,
        json_parser=FakeJSONParser(),
        search_engine=FakeSearchEngine(),
        web_reader=FakeWebReader(),
        source_storage=FakeSourceStorage(),
        file_storage=FakeFileStorage(),
    )


async def test_factory_builds_all_research_roles_and_readonly_worker() -> None:
    llm = FakeLLM()
    factory = build_factory(llm)
    run = AgentRun(
        id="run-1",
        session_id="session-1",
        mode=AgentMode.RESEARCH_TEAM,
        goal="research",
    )
    sequencer = EventSequencer(run.id)

    components = factory.build_components(run, sequencer)

    assert isinstance(components.planner, ResearchPlannerAgent)
    assert isinstance(components.orchestrator, ResearchOrchestrator)
    assert isinstance(components.reviewer, CoverageReviewerAgent)
    assert isinstance(components.synthesizer, ResearchSynthesizerAgent)
    assert isinstance(components.citation_verifier, CitationVerifier)
    assert components.planner.runtime._budget is components.budget_manager

    task = AgentTask(
        id="task-1",
        run_id=run.id,
        plan_version=1,
        task_key="topic",
        description="topic",
        objective="topic",
        capability_profile=CapabilityProfile.RESEARCH_READONLY,
        acceptance_criteria=["evidence"],
    )
    attempt = TaskAttempt(
        run_id=run.id,
        task_id=task.id,
        attempt_number=1,
        agent_id="worker-1",
        agent_profile="worker",
        model_profile="default",
        status=AttemptStatus.RUNNING,
    )
    worker = components.orchestrator._worker_factory.create(
        task=task,
        attempt=attempt,
        memory_store=EphemeralMemoryStore(),
        emit=sequencer.publish,
    )

    result = await worker.execute(WorkerContext(
        run_id=run.id,
        goal=run.goal,
        task=task,
        remaining_attempts=1,
    ))

    tool_names = {
        schema["function"]["name"]
        for schema in llm.calls[0]["tools"]
    }
    assert result.summary == "done"
    assert tool_names == {"search_web", "web_read"}
    assert worker.runtime._context.run_id == run.id
    assert worker.runtime._context.task_id == task.id
    assert worker.runtime._context.attempt_id == attempt.id
    assert worker.runtime._context.agent_id == attempt.agent_id


def test_factory_creates_resource_free_team_flow() -> None:
    flow = build_factory(FakeLLM()).create("session-1")

    assert isinstance(flow, ResearchTeamFlow)
    assert flow.resource_requirements.sandbox is False
    assert flow.resource_requirements.browser is False
