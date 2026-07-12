import asyncio
from io import BytesIO

from app.infrastructure.external.skill_bundle_storage.oss_skill_bundle_storage import (
    OSSSkillBundleStorage,
)


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, key: str, body: bytes) -> None:
        self.objects[key] = body

    def get_object(self, key: str) -> BytesIO:
        return BytesIO(self.objects[key])

    def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


class FakeOSS:
    def __init__(self) -> None:
        self.bucket = FakeBucket()


def test_oss_bundle_storage_round_trip() -> None:
    async def scenario() -> None:
        oss = FakeOSS()
        storage = OSSSkillBundleStorage(oss)

        key = await storage.upload_bundle("skill-id", b"zip-bytes")

        assert key.startswith("skills/skill-id/")
        assert key.endswith(".zip")
        assert await storage.download_bundle(key) == b"zip-bytes"
        await storage.delete_bundle(key)
        assert key not in oss.bucket.objects

    asyncio.run(scenario())
