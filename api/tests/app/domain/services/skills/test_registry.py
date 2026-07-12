import asyncio
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.domain.services.skills.parser import SkillParser
from app.domain.services.skills.registry import SkillRegistry
from tests.app.domain.services.skills.fakes import (
    FakeSkillBundleStorage,
    FakeSkillRepository,
    FakeUnitOfWork,
)


def skill_zip(name: str, description: str) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        )
        archive.writestr(f"{name}/references/guide.md", description)
    return stream.getvalue()


def make_registry(
    repository: FakeSkillRepository,
    storage: FakeSkillBundleStorage,
) -> SkillRegistry:
    return SkillRegistry(
        uow_factory=lambda: FakeUnitOfWork(repository),
        bundle_storage=storage,
        parser=SkillParser(),
    )


def test_upsert_defaults_enabled_and_preserves_state_on_overwrite() -> None:
    async def scenario() -> None:
        repository = FakeSkillRepository()
        storage = FakeSkillBundleStorage()
        registry = make_registry(repository, storage)

        created = await registry.upsert_bundle(skill_zip("demo", "first"))
        assert created.enabled is True

        disabled = await registry.set_enabled(created.id, False)
        assert disabled is not None
        assert disabled.enabled is False

        replaced = await registry.upsert_bundle(skill_zip("demo", "second"))

        assert replaced.id == created.id
        assert replaced.enabled is False
        assert replaced.description == "second"
        assert created.bundle_key in storage.deleted_keys

    asyncio.run(scenario())


def test_snapshot_keeps_bundle_after_registry_changes() -> None:
    async def scenario() -> None:
        repository = FakeSkillRepository()
        storage = FakeSkillBundleStorage()
        registry = make_registry(repository, storage)
        created = await registry.upsert_bundle(skill_zip("demo", "first"))

        snapshot = await registry.create_enabled_snapshot()
        await registry.upsert_bundle(skill_zip("demo", "second"))
        await registry.delete_skill(created.id)

        assert snapshot[0].description == "first"
        assert snapshot[0].bundle_bytes is not None

    asyncio.run(scenario())


def test_snapshot_records_download_error() -> None:
    async def scenario() -> None:
        repository = FakeSkillRepository()
        storage = FakeSkillBundleStorage()
        registry = make_registry(repository, storage)
        created = await registry.upsert_bundle(skill_zip("demo", "first"))
        storage.fail_download_for.add(created.bundle_key)

        snapshot = await registry.create_enabled_snapshot()

        assert snapshot[0].bundle_bytes is None
        assert snapshot[0].bundle_load_error == "OSS unavailable"

    asyncio.run(scenario())


def test_save_failure_removes_new_bundle_and_keeps_current_record() -> None:
    async def scenario() -> None:
        repository = FakeSkillRepository()
        storage = FakeSkillBundleStorage()
        registry = make_registry(repository, storage)
        created = await registry.upsert_bundle(skill_zip("demo", "first"))
        repository.fail_save = True

        with pytest.raises(RuntimeError, match="database unavailable"):
            await registry.upsert_bundle(skill_zip("demo", "second"))

        assert repository.records[created.id].description == "first"
        assert storage.deleted_keys[-1] != created.bundle_key
        assert created.bundle_key in storage.objects

    asyncio.run(scenario())


def test_disabled_skills_are_not_in_new_task_snapshot() -> None:
    async def scenario() -> None:
        repository = FakeSkillRepository()
        storage = FakeSkillBundleStorage()
        registry = make_registry(repository, storage)
        created = await registry.upsert_bundle(skill_zip("demo", "first"))
        await registry.set_enabled(created.id, False)

        assert await registry.create_enabled_snapshot() == ()

    asyncio.run(scenario())
