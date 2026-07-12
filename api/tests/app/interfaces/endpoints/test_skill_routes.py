from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.domain.models.skill import Skill
from app.interfaces.endpoints import skill_routes
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_skill_service


class FakeSkillService:
    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {}

    async def upload(self, file) -> Skill:
        if await file.read() == b"invalid":
            raise BadRequestError("无法读取 Skill ZIP")
        skill = Skill(
            id="skill-id",
            name="demo",
            description="demo description",
            skill_md="---\nname: demo\ndescription: demo description\n---\n",
            root_path="demo",
            bundle_key="private-key",
        )
        self.skills[skill.id] = skill
        return skill

    async def list_skills(self) -> list[Skill]:
        return list(self.skills.values())

    async def get_skill(self, skill_id: str) -> Skill:
        if skill_id not in self.skills:
            raise NotFoundError("Skill 不存在")
        return self.skills[skill_id]

    async def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        skill = await self.get_skill(skill_id)
        skill.enabled = enabled
        return skill

    async def delete_skill(self, skill_id: str) -> None:
        await self.get_skill(skill_id)
        self.skills.pop(skill_id)


def make_client(service: FakeSkillService) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(skill_routes.router, prefix="/api")
    app.dependency_overrides[get_skill_service] = lambda: service
    return TestClient(app)


def test_skill_management_routes() -> None:
    service = FakeSkillService()
    client = make_client(service)

    uploaded = client.post(
        "/api/app-config/skills",
        files={"file": ("demo.zip", b"bundle", "application/zip")},
    )
    assert uploaded.status_code == 200
    skill_id = uploaded.json()["data"]["id"]
    assert "bundle_key" not in uploaded.json()["data"]

    listed = client.get("/api/app-config/skills").json()["data"]["skills"]
    assert listed == [
        {
            "id": "skill-id",
            "name": "demo",
            "description": "demo description",
            "enabled": True,
        }
    ]

    detail = client.get(f"/api/app-config/skills/{skill_id}").json()["data"]
    assert detail["skill_md"].startswith("---")
    assert "bundle_key" not in detail

    disabled = client.post(
        f"/api/app-config/skills/{skill_id}/enabled",
        json={"enabled": False},
    )
    assert disabled.json()["data"]["enabled"] is False

    deleted = client.post(
        f"/api/app-config/skills/{skill_id}/delete",
        json={},
    )
    assert deleted.status_code == 200
    assert client.get(f"/api/app-config/skills/{skill_id}").status_code == 404


def test_invalid_skill_upload_returns_bad_request() -> None:
    client = make_client(FakeSkillService())

    response = client.post(
        "/api/app-config/skills",
        files={"file": ("demo.zip", b"invalid", "application/zip")},
    )

    assert response.status_code == 400
    assert response.json()["msg"] == "无法读取 Skill ZIP"
