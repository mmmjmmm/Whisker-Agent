import uuid

from starlette.concurrency import run_in_threadpool

from app.domain.external.skill_bundle_storage import SkillBundleStorage
from app.infrastructure.storage.oss import OSS


class OSSSkillBundleStorage(SkillBundleStorage):
    """在 OSS 中保存当前 Skill ZIP。"""

    def __init__(self, oss: OSS) -> None:
        self._oss = oss

    async def upload_bundle(self, skill_id: str, bundle: bytes) -> str:
        key = f"skills/{skill_id}/{uuid.uuid4()}.zip"
        await run_in_threadpool(self._oss.bucket.put_object, key, bundle)
        return key

    async def download_bundle(self, key: str) -> bytes:
        response = await run_in_threadpool(self._oss.bucket.get_object, key)
        return await run_in_threadpool(response.read)

    async def delete_bundle(self, key: str) -> None:
        await run_in_threadpool(self._oss.bucket.delete_object, key)
