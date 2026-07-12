from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.skill import Skill

from .base import Base


class SkillModel(Base):
    """Skill ORM 模型。"""

    __tablename__ = "skills"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_skills_id"),
        UniqueConstraint("name", name="uq_skills_name"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    skill_md: Mapped[str] = mapped_column(Text, nullable=False)
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    bundle_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )

    @classmethod
    def from_domain(cls, skill: Skill) -> "SkillModel":
        return cls(**skill.model_dump(mode="python"))

    def to_domain(self) -> Skill:
        return Skill.model_validate(self, from_attributes=True)

    def update_from_domain(self, skill: Skill) -> None:
        for field, value in skill.model_dump(mode="python").items():
            setattr(self, field, value)
