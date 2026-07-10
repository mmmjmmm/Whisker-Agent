import asyncio
from typing import Callable, Protocol

from app.domain.models.memory import Memory
from app.domain.repositories.uow import IUnitOfWork


class AgentMemoryStore(Protocol):
    async def load(self, memory_key: str) -> Memory: ...

    async def save(self, memory_key: str, memory: Memory) -> None: ...


class SessionMemoryStore:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            session_id: str,
    ) -> None:
        self._uow_factory = uow_factory
        self._session_id = session_id

    async def load(self, memory_key: str) -> Memory:
        async with self._uow_factory() as uow:
            return await uow.session.get_memory(self._session_id, memory_key)

    async def save(self, memory_key: str, memory: Memory) -> None:
        async with self._uow_factory() as uow:
            await uow.session.save_memory(self._session_id, memory_key, memory)


class RunMemoryStore:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._memories: dict[str, Memory] = {}
        self._lock = asyncio.Lock()

    async def load(self, memory_key: str) -> Memory:
        async with self._lock:
            memory = self._memories.get(memory_key, Memory(messages=[]))
            return memory.model_copy(deep=True)

    async def save(self, memory_key: str, memory: Memory) -> None:
        async with self._lock:
            self._memories[memory_key] = memory.model_copy(deep=True)


class EphemeralMemoryStore(RunMemoryStore):
    def __init__(self) -> None:
        super().__init__(run_id="ephemeral")

