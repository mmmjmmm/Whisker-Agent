from app.domain.models.agent_run import (
    AgentMode,
    AgentRun,
    CapabilityProfile,
    RunStatus,
)
from app.domain.models.event import DoneEvent, ResearchPlanEvent
from app.domain.models.message import Message
from app.domain.models.research import (
    AttachmentIngestResult,
    CitationVerificationResult,
    DraftClaim,
    DraftReport,
    DraftSection,
    OrchestrationResult,
    ResearchPlan,
    ResearchTaskSpec,
    ReviewResult,
)
from app.domain.models.run_command import StartRunCommand
from app.domain.services.flows.base import FlowRequest
from app.domain.services.flows.research_team import (
    ResearchTeamComponents,
    ResearchTeamFlow,
)


class FakeRunRepository:
    def __init__(self) -> None:
        self.runs = {}
        self.add_count = 0

    async def add(self, run: AgentRun) -> None:
        self.add_count += 1
        self.runs[run.id] = run.model_copy(deep=True)

    async def get(self, run_id: str):
        run = self.runs.get(run_id)
        return run.model_copy(deep=True) if run is not None else None

    async def update(self, run: AgentRun) -> None:
        self.runs[run.id] = run.model_copy(deep=True)

    async def list_tasks(self, run_id: str):
        return []


class FakeResearchRepository:
    async def list_claims(self, run_id: str):
        return []

    async def list_evidence(self, run_id: str):
        return []

    async def list_sources(self, run_id: str):
        return []


class FakeUow:
    def __init__(self, runs, research) -> None:
        self.agent_run = runs
        self.research = research

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class FakePlanner:
    def __init__(self, plan: ResearchPlan) -> None:
        self.result = plan

    async def plan(self, goal, budget) -> ResearchPlan:
        return self.result

    async def repair_invalid_plan(self, goal, error, budget) -> ResearchPlan:
        return self.result


class FakeOrchestrator:
    def __init__(self) -> None:
        self.call_count = 0

    async def execute(self, plan, run, attachment_evidence_ids=None):
        self.call_count += 1
        return OrchestrationResult(
            run_status=RunStatus.COMPLETED,
            status_by_key={task.key: "completed" for task in plan.tasks},
        )


class FakeReviewer:
    def __init__(self, results: list[ReviewResult]) -> None:
        self.results = list(results)
        self.call_count = 0

    async def review(self, context) -> ReviewResult:
        self.call_count += 1
        return self.results.pop(0)


class FakeSynthesizer:
    async def synthesize(self, claims, evidence, review) -> DraftReport:
        return DraftReport(
            title="Report",
            summary="Summary",
            sections=[DraftSection(
                title="Result",
                claims=[DraftClaim(claim_id="none", rendered_text="No verified claims")],
            )],
        )


class FakeVerifier:
    async def verify(self, draft, claims, evidence, sources):
        return CitationVerificationResult(draft=draft, checks=[])


class FakeIngestor:
    async def ingest(self, run_id, attachment_ids):
        return AttachmentIngestResult()


class FakeRenderer:
    def render(self, draft, claims, evidence, sources) -> str:
        return "# Report\n"


def initial_plan() -> ResearchPlan:
    return ResearchPlan(
        title="Initial",
        goal="research",
        tasks=[ResearchTaskSpec(
            key="initial",
            description="initial",
            objective="initial",
            capability_profile=CapabilityProfile.RESEARCH_READONLY,
            acceptance_criteria=["evidence"],
        )],
    )


async def test_reviewer_can_add_only_one_repair_wave() -> None:
    repair_task = ResearchTaskSpec(
        key="repair",
        description="repair",
        objective="repair",
        capability_profile=CapabilityProfile.RESEARCH_READONLY,
        acceptance_criteria=["evidence"],
    )
    reviewer = FakeReviewer([
        ReviewResult(approved=False, repair_tasks=[repair_task]),
        ReviewResult(approved=False, repair_tasks=[repair_task]),
    ])
    orchestrator = FakeOrchestrator()
    components = ResearchTeamComponents(
        planner=FakePlanner(initial_plan()),
        orchestrator=orchestrator,
        reviewer=reviewer,
        synthesizer=FakeSynthesizer(),
        citation_verifier=FakeVerifier(),
    )
    runs = FakeRunRepository()
    research = FakeResearchRepository()
    flow = ResearchTeamFlow(
        uow_factory=lambda: FakeUow(runs, research),
        session_id="session-1",
        component_factory=lambda run, sequencer: components,
        attachment_ingestor=FakeIngestor(),
        renderer=FakeRenderer(),
        heartbeat_interval_seconds=0.01,
    )
    request = FlowRequest(
        command=StartRunCommand(
            run_id="run-1",
            session_id="session-1",
            mode=AgentMode.RESEARCH_TEAM,
            message="research",
        ),
        message=Message(message="research"),
    )

    events = [event async for event in flow.invoke(request)]

    assert reviewer.call_count == 2
    assert orchestrator.call_count == 2
    assert sum(isinstance(event, ResearchPlanEvent) for event in events) == 2
    assert sum(isinstance(event, DoneEvent) for event in events) == 1


async def test_flow_reuses_pending_run_created_by_service() -> None:
    reviewer = FakeReviewer([ReviewResult(approved=True)])
    components = ResearchTeamComponents(
        planner=FakePlanner(initial_plan()),
        orchestrator=FakeOrchestrator(),
        reviewer=reviewer,
        synthesizer=FakeSynthesizer(),
        citation_verifier=FakeVerifier(),
    )
    runs = FakeRunRepository()
    runs.runs["run-1"] = AgentRun(
        id="run-1",
        session_id="session-1",
        mode=AgentMode.RESEARCH_TEAM,
        status=RunStatus.PENDING,
        goal="research",
    )
    flow = ResearchTeamFlow(
        uow_factory=lambda: FakeUow(runs, FakeResearchRepository()),
        session_id="session-1",
        component_factory=lambda run, sequencer: components,
        attachment_ingestor=FakeIngestor(),
        renderer=FakeRenderer(),
    )
    request = FlowRequest(
        command=StartRunCommand(
            run_id="run-1",
            session_id="session-1",
            mode=AgentMode.RESEARCH_TEAM,
            message="research",
        ),
        message=Message(message="research"),
    )

    events = [event async for event in flow.invoke(request)]

    assert runs.add_count == 0
    assert sum(isinstance(event, DoneEvent) for event in events) == 1
