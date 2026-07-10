import asyncio
import uuid
from dataclasses import dataclass
from typing import Literal

from app.domain.external.llm import LLMUsage
from app.domain.models.agent_run import RunBudget, RunUsage
from app.domain.services.research.errors import (
    ResearchErrorCode,
    ResearchExecutionError,
)


class BudgetExceeded(ResearchExecutionError):
    def __init__(self, limit: str) -> None:
        self.limit = limit
        super().__init__(
            code=ResearchErrorCode.BUDGET_EXCEEDED,
            message=limit,
            retryable=False,
            scope="run",
        )


@dataclass(frozen=True)
class BudgetReservation:
    id: str
    kind: Literal["llm", "tool"]
    reserved_tokens: int


class RunBudgetManager:
    def __init__(self, budget: RunBudget) -> None:
        self.budget = budget
        self.usage = RunUsage()
        self._reserved_tokens = 0
        self._active_reservations: set[str] = set()
        self._lock = asyncio.Lock()

    async def reserve_llm(self, token_ceiling: int) -> BudgetReservation:
        if token_ceiling < 0:
            raise ValueError("token_ceiling must not be negative")
        async with self._lock:
            if self.usage.llm_calls >= self.budget.max_llm_calls:
                raise BudgetExceeded("max_llm_calls")
            projected_tokens = (
                self.usage.total_tokens
                + self._reserved_tokens
                + token_ceiling
            )
            if projected_tokens > self.budget.max_total_tokens:
                raise BudgetExceeded("max_total_tokens")

            reservation = BudgetReservation(
                id=str(uuid.uuid4()),
                kind="llm",
                reserved_tokens=token_ceiling,
            )
            self.usage.llm_calls += 1
            self._reserved_tokens += token_ceiling
            self._active_reservations.add(reservation.id)
            return reservation

    async def settle_llm(
            self,
            reservation: BudgetReservation,
            usage: LLMUsage,
    ) -> RunUsage:
        async with self._lock:
            if reservation.kind != "llm" or reservation.id not in self._active_reservations:
                raise ValueError("unknown or settled LLM reservation")
            self._active_reservations.remove(reservation.id)
            self._reserved_tokens -= reservation.reserved_tokens
            self.usage.input_tokens += usage.input_tokens
            self.usage.output_tokens += usage.output_tokens
            self.usage.total_tokens += usage.total_tokens
            return self.usage.model_copy(deep=True)

    async def reserve_tool(self) -> BudgetReservation:
        async with self._lock:
            if self.usage.tool_calls >= self.budget.max_tool_calls:
                raise BudgetExceeded("max_tool_calls")
            self.usage.tool_calls += 1
            return BudgetReservation(
                id=str(uuid.uuid4()),
                kind="tool",
                reserved_tokens=0,
            )

    async def snapshot(self) -> RunUsage:
        async with self._lock:
            return self.usage.model_copy(deep=True)
