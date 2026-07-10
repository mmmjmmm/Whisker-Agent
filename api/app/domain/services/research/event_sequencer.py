import asyncio
from collections.abc import AsyncGenerator

from app.domain.models.event import BaseEvent


class EventSequencer:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue()
        self.sequence_no = 0
        self._closed = False

    async def publish(self, event: BaseEvent) -> None:
        if self._closed:
            raise RuntimeError("event sequencer is closed")
        await self.queue.put(event)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self.queue.put(None)

    async def events(self) -> AsyncGenerator[BaseEvent, None]:
        while True:
            event = await self.queue.get()
            if event is None:
                return
            self.sequence_no += 1
            event.sequence_no = self.sequence_no
            event.run_id = self.run_id
            yield event
