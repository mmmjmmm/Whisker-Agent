import pytest

from app.application.errors.exceptions import RunAlreadyActiveError
from app.domain.models.agent_run import AgentMode, AgentRun, RunStatus
from app.domain.models.session import SessionStatus

from .test_agent_service_chat import FakeTask, build_service


async def test_active_research_run_rejects_new_message() -> None:
    service, session, _sessions, runs = build_service()
    active = AgentRun(
        id="run-active",
        session_id=session.id,
        mode=AgentMode.RESEARCH_TEAM,
        status=RunStatus.RUNNING,
        goal="existing research",
    )
    await runs.add(active)

    with pytest.raises(RunAlreadyActiveError) as exc:
        await service.prepare_chat(
            session.id,
            message="new message",
            mode=AgentMode.REACT,
        )

    assert exc.value.data["run_id"] == active.id


async def test_running_react_accepts_another_react_message() -> None:
    service, session, _sessions, _runs = build_service()
    existing_task = FakeTask(task_runner=object())
    session.task_id = existing_task.id
    session.status = SessionStatus.RUNNING

    prepared = await service.prepare_chat(
        session.id,
        message="steer",
        mode=AgentMode.REACT,
    )

    assert prepared.command.mode == AgentMode.REACT
    assert prepared.created_task is False
    assert prepared.task is existing_task
    assert FakeTask.created_count == 1


async def test_running_react_rejects_switch_to_team() -> None:
    service, session, _sessions, _runs = build_service()
    existing_task = FakeTask(task_runner=object())
    session.task_id = existing_task.id
    session.status = SessionStatus.RUNNING

    with pytest.raises(RunAlreadyActiveError):
        await service.prepare_chat(
            session.id,
            message="switch",
            mode=AgentMode.RESEARCH_TEAM,
        )
