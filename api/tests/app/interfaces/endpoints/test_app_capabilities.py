from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.interfaces.endpoints.app_config_routes import router
from core.config import Settings, get_settings


def test_capabilities_do_not_expose_secrets() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_settings] = lambda: Settings(
        research_team_enabled=False,
        oss_access_key_id="secret-access-key",
    )

    with TestClient(app) as client:
        response = client.get("/api/app-config/capabilities")

    assert response.status_code == 200
    assert response.json()["data"] == {"research_team": False}
    assert "secret-access-key" not in response.text
    assert "api_key" not in response.text
