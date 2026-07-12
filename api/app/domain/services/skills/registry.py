import logging
from datetime import datetime
from typing import Callable

from app.domain.external.skill_bundle_storage import SkillBundleStorage
from app.domain.models.skill import Skill, SkillSnapshot
from app.domain.repositories.uow import IUnitOfWork

from .parser import SkillParser

logger = logging.getLogger(__name__)


class SkillRegistry:
    """系统全局 Skill 的唯一管理入口。"""

    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        bundle_storage: SkillBundleStorage,
        parser: SkillParser,
    ) -> None:
        self._uow_factory = uow_factory
        self._bundle_storage = bundle_storage
        self._parser = parser

    async def list_skills(self) -> list[Skill]:
        async with self._uow_factory() as uow:
            return await uow.skill.get_all()

    async def get_skill(self, skill_id: str) -> Skill | None:
        async with self._uow_factory() as uow:
            return await uow.skill.get_by_id(skill_id)

    async def upsert_bundle(self, bundle: bytes) -> Skill:
        parsed = self._parser.parse(bundle)
        async with self._uow_factory() as uow:
            current = await uow.skill.get_by_name(parsed.name)

        now = datetime.now()
        if current is None:
            skill = Skill(
                name=parsed.name,
                description=parsed.description,
                skill_md=parsed.skill_md,
                root_path=parsed.root_path,
                updated_at=now,
            )
            old_key = ""
        else:
            skill = Skill(
                id=current.id,
                name=parsed.name,
                description=parsed.description,
                skill_md=parsed.skill_md,
                root_path=parsed.root_path,
                bundle_key=current.bundle_key,
                enabled=current.enabled,
                created_at=current.created_at,
                updated_at=now,
            )
            old_key = current.bundle_key

        new_key = await self._bundle_storage.upload_bundle(skill.id, bundle)
        skill.bundle_key = new_key
        try:
            async with self._uow_factory() as uow:
                await uow.skill.save(skill)
                await uow.commit()
        except Exception:
            await self._delete_bundle_best_effort(new_key)
            raise

        if old_key and old_key != new_key:
            await self._delete_bundle_best_effort(old_key)
        return skill

    async def set_enabled(
        self,
        skill_id: str,
        enabled: bool,
    ) -> Skill | None:
        async with self._uow_factory() as uow:
            skill = await uow.skill.get_by_id(skill_id)
            if skill is None:
                return None
            skill.enabled = enabled
            skill.updated_at = datetime.now()
            await uow.skill.save(skill)
            await uow.commit()
            return skill

    async def delete_skill(self, skill_id: str) -> bool:
        async with self._uow_factory() as uow:
            skill = await uow.skill.get_by_id(skill_id)
            if skill is None:
                return False
            await uow.skill.delete_by_id(skill_id)
            await uow.commit()

        await self._delete_bundle_best_effort(skill.bundle_key)
        return True

    async def create_enabled_snapshot(self) -> tuple[SkillSnapshot, ...]:
        async with self._uow_factory() as uow:
            skills = await uow.skill.get_enabled()

        snapshots: list[SkillSnapshot] = []
        for skill in skills:
            try:
                bundle = await self._bundle_storage.download_bundle(
                    skill.bundle_key
                )
                load_error = None
            except Exception as exc:
                bundle = None
                load_error = str(exc)
            snapshots.append(
                SkillSnapshot(
                    id=skill.id,
                    name=skill.name,
                    description=skill.description,
                    skill_md=skill.skill_md,
                    root_path=skill.root_path,
                    bundle_bytes=bundle,
                    bundle_load_error=load_error,
                )
            )
        return tuple(snapshots)

    async def _delete_bundle_best_effort(self, key: str) -> None:
        if not key:
            return
        try:
            await self._bundle_storage.delete_bundle(key)
        except Exception as exc:
            logger.warning("清理 Skill ZIP[%s]失败: %s", key, exc)
