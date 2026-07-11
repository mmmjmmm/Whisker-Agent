from collections.abc import Callable

from pydantic import BaseModel, Field

from app.domain.models.session import SessionStatus
from app.domain.repositories.uow import IUnitOfWork


class RecoveryResult(BaseModel):
    run_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)


class RunRecoveryService:
    """Converges active research runs left behind by a prior API process."""

    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def interrupt_orphaned_runs(self, reason: str) -> RecoveryResult:
        async with self._uow_factory() as uow:
            interrupted = await uow.agent_run.mark_active_interrupted(reason)
            session_ids = list(
                dict.fromkeys(item.session_id for item in interrupted)
            )
            for session_id in session_ids:
                await uow.session.update_status(
                    session_id,
                    SessionStatus.COMPLETED,
                )

        return RecoveryResult(
            run_ids=[item.run_id for item in interrupted],
            session_ids=session_ids,
        )
