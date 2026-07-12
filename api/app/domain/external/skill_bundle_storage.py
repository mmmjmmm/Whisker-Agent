from typing import Protocol


class SkillBundleStorage(Protocol):
    """Skill ZIP 对象存储协议。"""

    async def upload_bundle(self, skill_id: str, bundle: bytes) -> str: ...

    async def download_bundle(self, key: str) -> bytes: ...

    async def delete_bundle(self, key: str) -> None: ...
