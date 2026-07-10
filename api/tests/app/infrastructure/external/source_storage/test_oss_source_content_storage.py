from app.infrastructure.external.source_storage.oss_source_content_storage import (
    OSSSourceContentStorage,
)


class FakeObject:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self) -> bytes:
        return self.content


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.headers: dict[str, dict[str, str]] = {}

    def put_object(self, key: str, content: bytes, headers=None) -> None:
        self.objects[key] = content
        self.headers[key] = headers or {}

    def get_object(self, key: str) -> FakeObject:
        return FakeObject(self.objects[key])


async def test_source_content_uses_deterministic_key_and_round_trips() -> None:
    bucket = FakeBucket()
    storage = OSSSourceContentStorage(bucket)

    first_key = await storage.put(
        run_id="run-1",
        content_hash="abc123",
        content=b"content",
        content_type="text/html",
    )
    second_key = await storage.put(
        run_id="run-1",
        content_hash="abc123",
        content=b"content",
        content_type="text/html",
    )

    assert first_key == second_key == "research/run-1/abc123"
    assert await storage.get(first_key) == b"content"
    assert bucket.headers[first_key]["Content-Type"] == "text/html"
