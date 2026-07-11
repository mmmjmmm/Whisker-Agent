from collections.abc import Callable

from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.search import SearchEngine
from app.domain.external.source_content_storage import SourceContentStorage
from app.domain.external.web_reader import WebReader
from app.domain.models.agent_run import AgentRun, AgentTask, TaskAttempt
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.coverage_reviewer import CoverageReviewerAgent
from app.domain.services.agents.research_planner import ResearchPlannerAgent
from app.domain.services.agents.research_synthesizer import (
    ResearchSynthesizerAgent,
)
from app.domain.services.agents.research_worker import ResearchWorker
from app.domain.services.flows.research_orchestrator import (
    ResearchOrchestrator,
    ResearchWorkerFactory,
)
from app.domain.services.flows.research_team import (
    ResearchTeamComponents,
    ResearchTeamFlow,
)
from app.domain.services.research.agent_runtime import (
    AgentRuntimeContext,
    TeamAgentRuntime,
)
from app.domain.services.research.attachment_ingestor import AttachmentIngestor
from app.domain.services.research.budget import RunBudgetManager
from app.domain.services.research.citation_verifier import CitationVerifier
from app.domain.services.research.event_sequencer import EventSequencer
from app.domain.services.research.evidence_normalizer import EvidenceNormalizer
from app.domain.services.research.final_report_renderer import FinalReportRenderer
from app.domain.services.research.memory_store import (
    EphemeralMemoryStore,
    RunMemoryStore,
)
from app.domain.services.research.tool_policy import ToolPolicy
from app.domain.services.research.telemetry import (
    NoopResearchTelemetry,
    ResearchTelemetry,
)
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.web_read import WebReadTool


class DefaultResearchWorkerFactory(ResearchWorkerFactory):
    def __init__(
            self,
            run: AgentRun,
            llm: LLM,
            tool_policy: ToolPolicy,
            budget: RunBudgetManager,
            json_parser: JSONParser,
            telemetry: ResearchTelemetry,
    ) -> None:
        self._run = run
        self._llm = llm
        self._tool_policy = tool_policy
        self._budget = budget
        self._json_parser = json_parser
        self._telemetry = telemetry

    def create(
            self,
            *,
            task: AgentTask,
            attempt: TaskAttempt,
            memory_store: EphemeralMemoryStore,
            emit,
    ) -> ResearchWorker:
        return ResearchWorker(TeamAgentRuntime(
            llm=self._llm,
            tool_policy=self._tool_policy,
            budget=self._budget,
            memory_store=memory_store,
            json_parser=self._json_parser,
            context=AgentRuntimeContext(
                session_id=self._run.session_id,
                run_id=self._run.id,
                task_id=task.id,
                attempt_id=attempt.id,
                agent_id=attempt.agent_id,
                agent_profile=attempt.agent_profile,
            ),
            emit=emit,
            telemetry=self._telemetry,
        ))


class ResearchTeamFlowFactory:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            llm: LLM,
            json_parser: JSONParser,
            search_engine: SearchEngine,
            web_reader: WebReader,
            source_storage: SourceContentStorage,
            file_storage: FileStorage,
            telemetry: ResearchTelemetry | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._llm = llm
        self._json_parser = json_parser
        self._search_engine = search_engine
        self._web_reader = web_reader
        self._source_storage = source_storage
        self._file_storage = file_storage
        self._telemetry = telemetry or NoopResearchTelemetry()

    def create(self, session_id: str) -> ResearchTeamFlow:
        return ResearchTeamFlow(
            uow_factory=self._uow_factory,
            session_id=session_id,
            component_factory=self.build_components,
            attachment_ingestor=AttachmentIngestor(
                file_storage=self._file_storage,
                uow_factory=self._uow_factory,
            ),
            renderer=FinalReportRenderer(),
            telemetry=self._telemetry,
        )

    def build_components(
            self,
            run: AgentRun,
            sequencer: EventSequencer,
    ) -> ResearchTeamComponents:
        budget = RunBudgetManager(run.budget_snapshot)
        tool_policy = ToolPolicy([
            SearchTool(self._search_engine),
            WebReadTool(self._web_reader),
        ])
        run_memory = RunMemoryStore(run.id)

        def runtime(agent_profile: str) -> TeamAgentRuntime:
            return TeamAgentRuntime(
                llm=self._llm,
                tool_policy=tool_policy,
                budget=budget,
                memory_store=run_memory,
                json_parser=self._json_parser,
                context=AgentRuntimeContext(
                    session_id=run.session_id,
                    run_id=run.id,
                    agent_id=f"{run.id}:{agent_profile}",
                    agent_profile=agent_profile,
                ),
                emit=sequencer.publish,
                telemetry=self._telemetry,
            )

        normalizer = EvidenceNormalizer(
            reader=self._web_reader,
            source_storage=self._source_storage,
            uow_factory=self._uow_factory,
            telemetry=self._telemetry,
        )
        worker_factory = DefaultResearchWorkerFactory(
            run=run,
            llm=self._llm,
            tool_policy=tool_policy,
            budget=budget,
            json_parser=self._json_parser,
            telemetry=self._telemetry,
        )
        return ResearchTeamComponents(
            planner=ResearchPlannerAgent(runtime("planner")),
            orchestrator=ResearchOrchestrator(
                uow_factory=self._uow_factory,
                worker_factory=worker_factory,
                normalizer=normalizer,
                event_sequencer=sequencer,
                telemetry=self._telemetry,
            ),
            reviewer=CoverageReviewerAgent(runtime("reviewer")),
            synthesizer=ResearchSynthesizerAgent(runtime("synthesizer")),
            citation_verifier=CitationVerifier(
                runtime=runtime("citation_verifier"),
                source_storage=self._source_storage,
            ),
            budget_manager=budget,
        )
