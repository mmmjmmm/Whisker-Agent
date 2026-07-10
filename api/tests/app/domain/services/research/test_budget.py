import asyncio

import pytest

from app.domain.external.llm import LLMUsage
from app.domain.models.agent_run import RunBudget
from app.domain.services.research.budget import BudgetExceeded, RunBudgetManager


@pytest.mark.asyncio
async def test_parallel_reservations_cannot_exceed_llm_limit() -> None:
    manager = RunBudgetManager(RunBudget(max_llm_calls=1))

    results = await asyncio.gather(
        manager.reserve_llm(100),
        manager.reserve_llm(100),
        return_exceptions=True,
    )

    assert sum(isinstance(item, BudgetExceeded) for item in results) == 1


@pytest.mark.asyncio
async def test_parallel_reservations_include_reserved_tokens() -> None:
    manager = RunBudgetManager(
        RunBudget(max_llm_calls=2, max_total_tokens=1_000)
    )

    results = await asyncio.gather(
        manager.reserve_llm(600),
        manager.reserve_llm(600),
        return_exceptions=True,
    )

    assert sum(isinstance(item, BudgetExceeded) for item in results) == 1


@pytest.mark.asyncio
async def test_settlement_releases_reservation_and_records_actual_usage() -> None:
    manager = RunBudgetManager(
        RunBudget(max_llm_calls=2, max_total_tokens=1_000)
    )
    reservation = await manager.reserve_llm(700)

    usage = await manager.settle_llm(
        reservation,
        LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150),
    )

    assert usage.llm_calls == 1
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.total_tokens == 150
    assert await manager.reserve_llm(700)


@pytest.mark.asyncio
async def test_failed_tool_reservation_still_counts_call() -> None:
    manager = RunBudgetManager(RunBudget(max_tool_calls=1))

    assert await manager.reserve_tool()
    with pytest.raises(BudgetExceeded, match="max_tool_calls"):
        await manager.reserve_tool()
