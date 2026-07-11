from app.domain.models.agent_run import (
    AgentMode,
    AgentRun,
    CapabilityProfile,
    RunStatus,
)
from app.domain.models.event import DoneEvent, MessageEvent
from app.domain.models.message import Message
from app.domain.models.research import (
    AttachmentIngestResult,
    CitationCheck,
    CitationVerificationResult,
    ClaimSupportStatus,
    DraftClaim,
    DraftReport,
    DraftSection,
    EvidenceExcerpt,
    OrchestrationResult,
    ResearchClaim,
    ResearchPlan,
    ResearchSource,
    ResearchTaskSpec,
    ReviewResult,
)
from app.domain.models.run_command import StartRunCommand
from app.domain.services.flows.base import FlowRequest
from app.domain.services.flows.research_team import (
    ResearchTeamComponents,
    ResearchTeamFlow,
)


class RunRepository:
    def __init__(self, run) -> None:
        self.run = run

    async def get(self, run_id):
        return self.run.model_copy(deep=True) if self.run.id == run_id else None

    async def update(self, run) -> None:
        self.run = run.model_copy(deep=True)

    async def add(self, run) -> None:
        self.run = run.model_copy(deep=True)

    async def list_tasks(self, _run_id):
        return []


class ResearchRepository:
    def __init__(self, claim, evidence, source) -> None:
        self.claim = claim
        self.evidence = evidence
        self.source = source

    async def list_claims(self, _run_id):
        return [self.claim.model_copy(deep=True)]

    async def list_evidence(self, _run_id):
        return [self.evidence.model_copy(deep=True)]

    async def list_sources(self, _run_id):
        return [self.source.model_copy(deep=True)]

    async def update_claim(self, claim) -> None:
        self.claim = claim.model_copy(deep=True)


class Uow:
    def __init__(self, runs, research) -> None:
        self.agent_run = runs
        self.research = research

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class Planner:
    async def plan(self, goal, budget):
        return ResearchPlan(
            title="Research",
            goal=goal,
            tasks=[ResearchTaskSpec(
                key="topic",
                description="topic",
                objective="topic",
                capability_profile=CapabilityProfile.RESEARCH_READONLY,
                acceptance_criteria=["evidence"],
            )],
        )


class Orchestrator:
    async def execute(self, plan, run, attachment_evidence_ids=None):
        return OrchestrationResult(
            run_status=RunStatus.COMPLETED,
            status_by_key={"topic": "completed"},
        )


class Reviewer:
    async def review(self, context):
        return ReviewResult(approved=True)


class Synthesizer:
    async def synthesize(self, claims, evidence, review):
        assert claims
        assert all(
            claim.support_status == ClaimSupportStatus.SUPPORTED
            for claim in claims
        )
        return DraftReport(
            title="Report",
            summary="Verified summary",
            sections=[DraftSection(
                title="Result",
                claims=[DraftClaim(
                    claim_id=claims[0].id,
                    rendered_text=claims[0].text,
                )],
            )],
        )


class Verifier:
    def __init__(self) -> None:
        self.preverify_calls = 0

    async def verify_claims(self, claims, evidence, sources):
        self.preverify_calls += 1
        return [CitationCheck(
            claim_id=claims[0].id,
            status=ClaimSupportStatus.SUPPORTED,
            reason="direct support",
        )]

    async def verify(self, draft, claims, evidence, sources):
        return CitationVerificationResult(
            draft=draft,
            checks=[CitationCheck(
                claim_id=claims[0].id,
                status=ClaimSupportStatus.SUPPORTED,
                reason="preverified",
            )],
        )


class Ingestor:
    async def ingest(self, run_id, attachment_ids):
        return AttachmentIngestResult()


class Renderer:
    def render(self, draft, claims, evidence, sources):
        return "# Report\n\nVerified fact.\n"


async def test_verified_claim_reaches_report_with_ordered_single_done() -> None:
    run = AgentRun(
        id="run-1",
        session_id="session-1",
        mode=AgentMode.RESEARCH_TEAM,
        status=RunStatus.PENDING,
        goal="research",
    )
    source = ResearchSource(
        id="source-1",
        run_id=run.id,
        canonical_url="https://example.com/",
        original_url="https://example.com/",
        title="Example",
        domain="example.com",
        content_type="text/html",
        content_hash="hash",
        object_storage_key="research/run-1/hash",
    )
    evidence = EvidenceExcerpt(
        id="evidence-1",
        source_id=source.id,
        run_id=run.id,
        locator="p1",
        excerpt="Verified fact.",
        excerpt_hash="excerpt-hash",
    )
    claim = ResearchClaim(
        id="claim-1",
        run_id=run.id,
        task_id="task-1",
        text="Verified fact.",
        importance=2,
        confidence=0.9,
        evidence_ids=[evidence.id],
    )
    runs = RunRepository(run)
    research = ResearchRepository(claim, evidence, source)
    verifier = Verifier()
    flow = ResearchTeamFlow(
        uow_factory=lambda: Uow(runs, research),
        session_id=run.session_id,
        component_factory=lambda _run, _events: ResearchTeamComponents(
            planner=Planner(),
            orchestrator=Orchestrator(),
            reviewer=Reviewer(),
            synthesizer=Synthesizer(),
            citation_verifier=verifier,
        ),
        attachment_ingestor=Ingestor(),
        renderer=Renderer(),
    )
    request = FlowRequest(
        command=StartRunCommand(
            run_id=run.id,
            session_id=run.session_id,
            mode=AgentMode.RESEARCH_TEAM,
            message=run.goal,
        ),
        message=Message(message=run.goal),
    )

    events = [event async for event in flow.invoke(request)]

    assert runs.run.status == RunStatus.COMPLETED
    assert verifier.preverify_calls == 1
    assert any(
        isinstance(event, MessageEvent) and "Verified fact" in event.message
        for event in events
    )
    assert sum(isinstance(event, DoneEvent) for event in events) == 1
    assert [event.sequence_no for event in events] == list(
        range(1, len(events) + 1)
    )
