from pydantic import BaseModel, ConfigDict, Field


class SkillListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    enabled: bool


class SkillDetail(SkillListItem):
    skill_md: str


class ListSkillsResponse(BaseModel):
    skills: list[SkillListItem] = Field(default_factory=list)
