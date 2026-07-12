from app.domain.models.skill import Skill
from app.infrastructure.models.skill import SkillModel


def test_skill_model_round_trip() -> None:
    skill = Skill(
        id="skill-id",
        name="demo",
        description="demo description",
        skill_md="---\nname: demo\ndescription: demo description\n---\n",
        root_path="demo",
        bundle_key="skills/skill-id/upload.zip",
        enabled=False,
    )

    assert SkillModel.from_domain(skill).to_domain() == skill
