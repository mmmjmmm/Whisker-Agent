import re
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.domain.external.source_content_storage import SourceContentStorage


SAFE_KEY_PART = re.compile(r"^[a-zA-Z0-9_-]+$")


class OSSSourceContentStorage(SourceContentStorage):
    def __init__(self, bucket: Any) -> None:
        self._bucket = bucket

    async def put(
            self,
            run_id: str,
            content_hash: str,
            content: bytes,
            content_type: str,
    ) -> str:
        self._validate_key_part(run_id, "run_id")
        self._validate_key_part(content_hash, "content_hash")
        object_key = f"research/{run_id}/{content_hash}"
        await run_in_threadpool(
            self._bucket.put_object,
            object_key,
            content,
            headers={"Content-Type": content_type},
        )
        return object_key

    async def get(self, object_key: str) -> bytes:
        stored = await run_in_threadpool(self._bucket.get_object, object_key)
        return await run_in_threadpool(stored.read)

    @staticmethod
    def _validate_key_part(value: str, name: str) -> None:
        if not SAFE_KEY_PART.fullmatch(value):
            raise ValueError(f"invalid {name}")

