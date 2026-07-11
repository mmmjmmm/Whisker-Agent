from types import SimpleNamespace

import pytest

from app.infrastructure.repositories.db_agent_run_repository import (
    DBAgentRunRepository,
)


class _Result:
    def __init__(self, records: list[SimpleNamespace]) -> None:
        self._records = records

    def scalars(self):
        return self

    def all(self) -> list[SimpleNamespace]:
        return self._records


class _Session:
    def __init__(self, results: list[_Result]) -> None:
        self._results = iter(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return next(self._results)


@pytest.mark.asyncio
async def test_interrupt_updates_run_task_and_attempt_in_one_repository_call() -> None:
    run = SimpleNamespace(
        id="run-1",
        session_id="session-1",
        status="running",
        error=None,
        finished_at=None,
        updated_at=None,
    )
    task = SimpleNamespace(status="running", error=None, updated_at=None)
    attempt = SimpleNamespace(
        status="running",
        error_type=None,
        error_message=None,
        finished_at=None,
    )
    session = _Session(
        [_Result([run]), _Result([task]), _Result([attempt])]
    )
    repository = DBAgentRunRepository(session)

    interrupted = await repository.mark_active_interrupted("process_started")

    assert [item.run_id for item in interrupted] == ["run-1"]
    assert run.status == "interrupted"
    assert run.error == {
        "type": "ProcessInterrupted",
        "message": "process_started",
    }
    assert task.status == "interrupted"
    assert task.error["type"] == "ProcessInterrupted"
    assert attempt.status == "interrupted"
    assert attempt.error_type == "ProcessInterrupted"
    assert run.finished_at == attempt.finished_at
    assert "agent_runs.mode" in str(session.statements[0])
