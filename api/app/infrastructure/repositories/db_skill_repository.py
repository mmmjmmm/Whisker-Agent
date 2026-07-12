from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.skill import Skill
from app.domain.repositories.skill_repository import SkillRepository
from app.infrastructure.models import SkillModel


class DBSkillRepository(SkillRepository):
    """PostgreSQL Skill 仓库。"""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, skill: Skill) -> None:
        result = await self.db_session.execute(
            select(SkillModel).where(SkillModel.id == skill.id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(SkillModel.from_domain(skill))
            return
        record.update_from_domain(skill)

    async def get_all(self) -> list[Skill]:
        result = await self.db_session.execute(
            select(SkillModel).order_by(SkillModel.name)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def get_enabled(self) -> list[Skill]:
        result = await self.db_session.execute(
            select(SkillModel)
            .where(SkillModel.enabled.is_(True))
            .order_by(SkillModel.name)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def get_by_id(self, skill_id: str) -> Skill | None:
        result = await self.db_session.execute(
            select(SkillModel).where(SkillModel.id == skill_id)
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def get_by_name(self, name: str) -> Skill | None:
        result = await self.db_session.execute(
            select(SkillModel).where(SkillModel.name == name)
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def delete_by_id(self, skill_id: str) -> None:
        await self.db_session.execute(
            delete(SkillModel).where(SkillModel.id == skill_id)
        )
