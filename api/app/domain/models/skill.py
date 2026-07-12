import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Skill(BaseModel):
    """系统全局 Skill。"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    skill_md: str
    root_path: str = ""
    bundle_key: str = ""
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ParsedSkill(BaseModel):
    """从 ZIP 中提取出的运行必需信息。"""

    name: str
    description: str
    skill_md: str
    root_path: str


class SkillSnapshot(BaseModel):
    """任务创建时固定的 Skill 快照。"""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    skill_md: str
    root_path: str
    bundle_bytes: bytes | None = None
    bundle_load_error: str | None = None
