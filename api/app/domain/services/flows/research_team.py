import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.domain.models.agent_run import AgentRun, RunStatus
from app.domain.models.event import (
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    ResearchPlanEvent,
    ResearchReviewEvent,
    ResearchSourceEvent,
    RunEvent,
)
from app.domain.models.research import (
    ClaimSupportStatus,
    ResearchPlan,
    ReviewContext,
)
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.flows.base import (
    BaseFlow,
    FlowRequest,
    FlowResourceRequirements,
)
from app.domain.services.research.event_sequencer import EventSequencer
from app.domain.services.research.task_graph import InvalidTaskGraph, TaskGraph


@dataclass
class ResearchTeamComponents:
    planner: Any
    orchestrator: Any
    reviewer: Any
    synthesizer: Any
    citation_verifier: Any
    budget_manager: Any | None = None


class ResearchTeamFlow(BaseFlow):
    resource_requirements = FlowResourceRequirements()

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            session_id: str,
            component_factory: Callable[
                [AgentRun, EventSequencer], ResearchTeamComponents
            ],
            attachment_ingestor: Any,
            renderer: Any,
            heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        self._uow_factory = uow_factory
        self._session_id = session_id
        self._component_factory = component_factory
        self._attachment_ingestor = attachment_ingestor
        self._renderer = renderer
        self._heartbeat_interval = heartbeat_interval_seconds
        self._done = True

    async def invoke(self, request: FlowRequest):
        self._done = False
        sequencer = EventSequencer(request.command.run_id)
        pipeline = asyncio.create_task(
            self._run_pipeline(request, sequencer)
        )
        try:
            async for event in sequencer.events():
                yield event
            await pipeline
        finally:
            if not pipeline.done():
                pipeline.cancel()
                await asyncio.gather(pipeline, return_exceptions=True)
            self._done = True

    @property
    def done(self) -> bool:
        return self._done

    async def _run_pipeline(
            self,
            request: FlowRequest,
            sequencer: EventSequencer,
    ) -> None:
        command = request.command
        run = await self._prepare_run(command)
        heartbeat_stop = asyncio.Event()
        heartbeat_task: asyncio.Task | None = None
        done_published = False
        try:
            await sequencer.publish(RunEvent(
                session_id=run.session_id,
                status=run.status,
                goal=run.goal,
            ))
            heartbeat_task = asyncio.create_task(
                self._heartbeat(run, heartbeat_stop)
            )
            components = self._component_factory(run, sequencer)
            async with asyncio.timeout(run.budget_snapshot.run_timeout_seconds):
                final_message = await self._execute_stages(
                    request,
                    run,
                    components,
                    sequencer,
                )
            await sequencer.publish(RunEvent(
                session_id=run.session_id,
                status=run.status,
                goal=run.goal,
                usage=run.usage.model_dump(mode="json"),
                error=run.error,
            ))
            await sequencer.publish(MessageEvent(
                session_id=run.session_id,
                role="assistant",
                message=final_message,
            ))
            await sequencer.publish(DoneEvent(session_id=run.session_id))
            done_published = True
        except TimeoutError:
            run.status = RunStatus.FAILED
            run.error = {
                "type": "RunTimeout",
                "message": "research run timed out",
            }
            await self._finish_run(run)
            await sequencer.publish(ErrorEvent(
                session_id=run.session_id,
                error="research run timed out",
            ))
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            run.error = {"type": "RunCancelled", "message": "run cancelled"}
            await self._finish_run(run)
            raise
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            await self._finish_run(run)
            await sequencer.publish(ErrorEvent(
                session_id=run.session_id,
                error=f"research team flow failed: {exc}",
            ))
        finally:
            if not done_published:
                await sequencer.publish(RunEvent(
                    session_id=run.session_id,
                    status=run.status,
                    goal=run.goal,
                    usage=run.usage.model_dump(mode="json"),
                    error=run.error,
                ))
                await sequencer.publish(DoneEvent(session_id=run.session_id))
            heartbeat_stop.set()
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            await sequencer.close()

    async def _execute_stages(
            self,
            request: FlowRequest,
            run: AgentRun,
            components: ResearchTeamComponents,
            sequencer: EventSequencer,
    ) -> str:
        attachments = await self._attachment_ingestor.ingest(
            run.id,
            request.command.attachment_ids,
        )
        for source in attachments.sources:
            await sequencer.publish(ResearchSourceEvent(
                session_id=run.session_id,
                source=source,
            ))

        plan = await components.planner.plan(run.goal, run.budget_snapshot)
        try:
            TaskGraph.build(plan.tasks, run.budget_snapshot)
        except InvalidTaskGraph as error:
            plan = await components.planner.repair_invalid_plan(
                run.goal,
                error,
                run.budget_snapshot,
            )
            TaskGraph.build(plan.tasks, run.budget_snapshot)

        run.plan_version = 1
        run.status = RunStatus.RUNNING
        await self._save_run(run)
        await sequencer.publish(ResearchPlanEvent(
            session_id=run.session_id,
            plan=plan,
            status="created",
        ))
        first_result = await components.orchestrator.execute(
            plan,
            run,
            attachment_evidence_ids=[item.id for item in attachments.evidence],
        )

        tasks, claims, evidence, sources = await self._load_research_data(run.id)
        run.status = RunStatus.REVIEWING
        await self._save_run(run)
        review = await components.reviewer.review(ReviewContext(
            run_id=run.id,
            goal=run.goal,
            plan=plan,
            tasks=tasks,
            claims=claims,
            evidence=evidence,
            sources=sources,
        ))
        await sequencer.publish(ResearchReviewEvent(
            session_id=run.session_id,
            review=review,
        ))
        orchestration_statuses = [first_result.run_status]

        if review.repair_tasks:
            combined_tasks = [*plan.tasks, *review.repair_tasks]
            TaskGraph.build(combined_tasks, run.budget_snapshot)
            repair_keys = {task.key for task in review.repair_tasks}
            executable_repairs = [
                task.model_copy(update={
                    "dependencies": [
                        dependency
                        for dependency in task.dependencies
                        if dependency in repair_keys
                    ]
                })
                for task in review.repair_tasks
            ]
            repair_plan = ResearchPlan(
                title=f"{plan.title} - repair",
                goal=plan.goal,
                language=plan.language,
                source_strategy=plan.source_strategy,
                tasks=executable_repairs,
            )
            run.plan_version = 2
            run.status = RunStatus.RUNNING
            await self._save_run(run)
            await sequencer.publish(ResearchPlanEvent(
                session_id=run.session_id,
                plan=repair_plan,
                status="repair",
            ))
            repair_result = await components.orchestrator.execute(
                repair_plan,
                run,
                attachment_evidence_ids=[item.id for item in evidence],
            )
            orchestration_statuses.append(repair_result.run_status)
            tasks, claims, evidence, sources = await self._load_research_data(run.id)
            run.status = RunStatus.REVIEWING
            await self._save_run(run)
            review = await components.reviewer.review(ReviewContext(
                run_id=run.id,
                goal=run.goal,
                plan=ResearchPlan(
                    title=plan.title,
                    goal=plan.goal,
                    language=plan.language,
                    source_strategy=plan.source_strategy,
                    tasks=combined_tasks,
                ),
                tasks=tasks,
                claims=claims,
                evidence=evidence,
                sources=sources,
            ))
            await sequencer.publish(ResearchReviewEvent(
                session_id=run.session_id,
                review=review,
            ))

        run.status = RunStatus.SYNTHESIZING
        await self._save_run(run)
        draft = await components.synthesizer.synthesize(claims, evidence, review)
        verification = await components.citation_verifier.verify(
            draft,
            claims,
            evidence,
            sources,
        )
        self._apply_checks(claims, verification.checks)
        await self._save_claims(claims)

        if any(
            check.status == ClaimSupportStatus.UNSUPPORTED
            for check in verification.checks
        ):
            repaired_draft = await components.synthesizer.synthesize(
                claims,
                evidence,
                review,
            )
            verification = await components.citation_verifier.verify(
                repaired_draft,
                claims,
                evidence,
                sources,
            )
            self._apply_checks(claims, verification.checks)
            await self._save_claims(claims)

        if components.budget_manager is not None:
            run.usage = await components.budget_manager.snapshot()

        verified_claim_ids = {
            draft_claim.claim_id
            for section in verification.draft.sections
            for draft_claim in section.claims
        }
        has_verified_claim = any(
            claim.id in verified_claim_ids
            and claim.support_status in {
                ClaimSupportStatus.SUPPORTED,
                ClaimSupportStatus.PARTIALLY_SUPPORTED,
            }
            for claim in claims
        )
        degraded = any(
            status in {RunStatus.PARTIAL, RunStatus.FAILED}
            for status in orchestration_statuses
        )
        run.status = (
            RunStatus.FAILED
            if not has_verified_claim
            else RunStatus.PARTIAL
            if degraded or not review.approved
            else RunStatus.COMPLETED
        )
        await self._finish_run(run)
        if not has_verified_claim:
            return "研究未获得可验证证据，未生成事实性结论。"
        return self._renderer.render(
            verification.draft,
            claims,
            evidence,
            sources,
        )

    async def _heartbeat(self, run: AgentRun, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._heartbeat_interval,
                )
            except TimeoutError:
                run.heartbeat_at = datetime.now(timezone.utc)
                await self._save_run(run)

    async def _add_run(self, run: AgentRun) -> None:
        async with self._uow_factory() as uow:
            await uow.agent_run.add(run)

    async def _prepare_run(self, command) -> AgentRun:
        async with self._uow_factory() as uow:
            run = await uow.agent_run.get(command.run_id)

        now = datetime.now(timezone.utc)
        if run is None:
            run = AgentRun(
                id=command.run_id,
                session_id=command.session_id,
                mode=command.mode,
                goal=command.message,
                budget_snapshot=command.budget,
                status=RunStatus.PLANNING,
                started_at=now,
            )
            await self._add_run(run)
            return run

        if run.session_id != command.session_id or run.mode != command.mode:
            raise ValueError("run command does not match persisted run")
        if run.status != RunStatus.PENDING:
            raise ValueError(f"run is not pending: {run.status.value}")
        run.goal = command.message
        run.budget_snapshot = command.budget
        run.status = RunStatus.PLANNING
        run.started_at = run.started_at or now
        run.heartbeat_at = now
        await self._save_run(run)
        return run

    async def _save_run(self, run: AgentRun) -> None:
        run.updated_at = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            await uow.agent_run.update(run)

    async def _finish_run(self, run: AgentRun) -> None:
        run.finished_at = datetime.now(timezone.utc)
        await self._save_run(run)

    async def _load_research_data(self, run_id: str):
        async with self._uow_factory() as uow:
            tasks = await uow.agent_run.list_tasks(run_id)
            claims = await uow.research.list_claims(run_id)
            evidence = await uow.research.list_evidence(run_id)
            sources = await uow.research.list_sources(run_id)
        return tasks, claims, evidence, sources

    async def _save_claims(self, claims) -> None:
        if not claims:
            return
        async with self._uow_factory() as uow:
            for claim in claims:
                await uow.research.update_claim(claim)

    @staticmethod
    def _apply_checks(claims, checks) -> None:
        claims_by_id = {claim.id: claim for claim in claims}
        for check in checks:
            claim = claims_by_id.get(check.claim_id)
            if claim is not None:
                claim.support_status = check.status
