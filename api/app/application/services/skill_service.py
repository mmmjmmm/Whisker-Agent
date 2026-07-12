from fastapi import UploadFile

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.models.skill import Skill
from app.domain.services.skills.parser import SkillParseError
from app.domain.services.skills.registry import SkillRegistry


class SkillService:
    """设置模块的 Skill 应用服务。"""

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def upload(self, file: UploadFile) -> Skill:
        try:
            return await self._registry.upsert_bundle(await file.read())
        except SkillParseError as exc:
            raise BadRequestError(str(exc)) from exc

    async def list_skills(self) -> list[Skill]:
        return await self._registry.list_skills()

    async def get_skill(self, skill_id: str) -> Skill:
        skill = await self._registry.get_skill(skill_id)
        if skill is None:
            raise NotFoundError("Skill 不存在")
        return skill

    async def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        skill = await self._registry.set_enabled(skill_id, enabled)
        if skill is None:
            raise NotFoundError("Skill 不存在")
        return skill

    async def delete_skill(self, skill_id: str) -> None:
        if not await self._registry.delete_skill(skill_id):
            raise NotFoundError("Skill 不存在")
