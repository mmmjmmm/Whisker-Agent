from app.domain.models.skill import Skill


class FakeSkillRepository:
    def __init__(self) -> None:
        self.records: dict[str, Skill] = {}
        self.fail_save = False

    async def save(self, skill: Skill) -> None:
        if self.fail_save:
            raise RuntimeError("database unavailable")
        self.records[skill.id] = skill.model_copy(deep=True)

    async def get_all(self) -> list[Skill]:
        return sorted(
            (item.model_copy(deep=True) for item in self.records.values()),
            key=lambda item: item.name,
        )

    async def get_enabled(self) -> list[Skill]:
        return [item for item in await self.get_all() if item.enabled]

    async def get_by_id(self, skill_id: str) -> Skill | None:
        skill = self.records.get(skill_id)
        return skill.model_copy(deep=True) if skill else None

    async def get_by_name(self, name: str) -> Skill | None:
        return next(
            (
                item.model_copy(deep=True)
                for item in self.records.values()
                if item.name == name
            ),
            None,
        )

    async def delete_by_id(self, skill_id: str) -> None:
        self.records.pop(skill_id, None)


class FakeUnitOfWork:
    def __init__(self, repository: FakeSkillRepository) -> None:
        self.skill = repository

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeSkillBundleStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted_keys: list[str] = []
        self.fail_download_for: set[str] = set()
        self.counter = 0

    async def upload_bundle(self, skill_id: str, bundle: bytes) -> str:
        self.counter += 1
        key = f"skills/{skill_id}/{self.counter}.zip"
        self.objects[key] = bundle
        return key

    async def download_bundle(self, key: str) -> bytes:
        if key in self.fail_download_for:
            raise RuntimeError("OSS unavailable")
        return self.objects[key]

    async def delete_bundle(self, key: str) -> None:
        self.deleted_keys.append(key)
        self.objects.pop(key, None)
