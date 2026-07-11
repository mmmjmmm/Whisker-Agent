from app.domain.models.agent_run import (
    AgentMode,
    AgentRun,
    AgentTask,
    CapabilityProfile,
    RunStatus,
    TaskStatus,
)
from app.domain.models.research import ResearchSource

from .test_agent_service_chat import FakeTask, build_service


async def test_get_run_tasks_and_sources_check_session_ownership() -> None:
    service, session, _sessions, runs = build_service()
    run = AgentRun(
        id="run-1",
        session_id=session.id,
        mode=AgentMode.RESEARCH_TEAM,
        status=RunStatus.RUNNING,
        goal="research",
    )
    task = AgentTask(
        id="research-task-1",
        run_id=run.id,
        plan_version=1,
        task_key="topic",
        description="topic",
        objective="topic",
        capability_profile=CapabilityProfile.RESEARCH_READONLY,
        acceptance_criteria=["evidence"],
    )
    source = ResearchSource(
        run_id=run.id,
        canonical_url="https://example.com/",
        original_url="https://example.com/",
        title="Example",
        domain="example.com",
        content_type="text/html",
        content_hash="hash",
        object_storage_key="research/run-1/hash",
    )
    await runs.add(run)
    runs.tasks[task.id] = task
    service._test_research_repository.sources.append(source)

    loaded = await service.get_run(session.id, run.id)
    tasks = await service.list_run_tasks(session.id, run.id)
    sources = await service.list_run_sources(session.id, run.id)

    assert loaded.id == run.id
    assert [item.id for item in tasks] == [task.id]
    assert [item.id for item in sources] == [source.id]


async def test_cancel_run_is_idempotent_and_cancels_non_terminal_tasks() -> None:
    service, session, _sessions, runs = build_service()
    process_task = FakeTask(task_runner=object())
    session.task_id = process_task.id
    run = AgentRun(
        id="run-1",
        session_id=session.id,
        mode=AgentMode.RESEARCH_TEAM,
        status=RunStatus.RUNNING,
        goal="research",
    )
    pending = AgentTask(
        id="pending-task",
        run_id=run.id,
        plan_version=1,
        task_key="pending",
        description="pending",
        objective="pending",
        capability_profile=CapabilityProfile.RESEARCH_READONLY,
        acceptance_criteria=["evidence"],
        status=TaskStatus.RUNNING,
    )
    completed = pending.model_copy(update={
        "id": "completed-task",
        "task_key": "completed",
        "status": TaskStatus.COMPLETED,
    })
    await runs.add(run)
    runs.tasks = {pending.id: pending, completed.id: completed}

    first = await service.cancel_run(session.id, run.id)
    second = await service.cancel_run(session.id, run.id)

    assert first.status == second.status == RunStatus.CANCELLED
    assert first.finished_at is not None
    assert runs.tasks[pending.id].status == TaskStatus.CANCELLED
    assert runs.tasks[completed.id].status == TaskStatus.COMPLETED
    assert process_task.cancel_count == 1
