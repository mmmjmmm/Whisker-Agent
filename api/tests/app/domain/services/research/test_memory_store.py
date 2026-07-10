from app.domain.models.memory import Memory
from app.domain.services.research.memory_store import (
    EphemeralMemoryStore,
    RunMemoryStore,
)


async def test_ephemeral_worker_attempts_do_not_share_messages() -> None:
    first = EphemeralMemoryStore()
    second = EphemeralMemoryStore()
    await first.save(
        "worker",
        Memory(messages=[{"role": "user", "content": "secret"}]),
    )

    assert (await second.load("worker")).messages == []


async def test_run_memory_returns_defensive_copies() -> None:
    store = RunMemoryStore(run_id="run-1")
    original = Memory(messages=[{"role": "user", "content": "original"}])
    await store.save("planner", original)

    loaded = await store.load("planner")
    loaded.add_message({"role": "assistant", "content": "changed"})

    assert len((await store.load("planner")).messages) == 1

