from typing import Protocol


class SourceContentStorage(Protocol):
    async def put(
            self,
            run_id: str,
            content_hash: str,
            content: bytes,
            content_type: str,
    ) -> str: ...

    async def get(self, object_key: str) -> bytes: ...

