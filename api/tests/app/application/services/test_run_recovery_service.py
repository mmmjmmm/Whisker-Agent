import pytest

from app.application.services.run_recovery_service import RunRecoveryService
from app.domain.models.agent_run import InterruptedRun
from app.domain.models.session import SessionStatus


class _RunRepository:
    def __init__(self) -> None:
        self.interrupted = [
            InterruptedRun(run_id="run-1", session_id="session-1"),
            InterruptedRun(run_id="run-2", session_id="session-1"),
        ]
        self.reasons: list[str] = []

    async def mark_active_interrupted(self, reason: str) -> list[InterruptedRun]:
        self.reasons.append(reason)
        result = self.interrupted
        self.interrupted = []
        return result


class _SessionRepository:
    def __init__(self) -> None:
        self.statuses: dict[str, SessionStatus] = {}

    async def update_status(
        self,
        session_id: str,
        status: SessionStatus,
    ) -> None:
        self.statuses[session_id] = status


class _UnitOfWork:
    def __init__(self) -> None:
        self.agent_run = _RunRepository()
        self.session = _SessionRepository()
        self.enter_count = 0

    async def __aenter__(self):
        self.enter_count += 1
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


@pytest.mark.asyncio
async def test_startup_marks_active_team_state_interrupted() -> None:
    uow = _UnitOfWork()
    service = RunRecoveryService(lambda: uow)

    result = await service.interrupt_orphaned_runs("process_started")

    assert result.run_ids == ["run-1", "run-2"]
    assert result.session_ids == ["session-1"]
    assert uow.agent_run.reasons == ["process_started"]
    assert uow.session.statuses == {
        "session-1": SessionStatus.COMPLETED,
    }


@pytest.mark.asyncio
async def test_startup_recovery_is_idempotent() -> None:
    uow = _UnitOfWork()
    service = RunRecoveryService(lambda: uow)

    await service.interrupt_orphaned_runs("process_started")
    second = await service.interrupt_orphaned_runs("process_started")

    assert second.run_ids == []
    assert second.session_ids == []
    assert uow.enter_count == 2
