from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models.agent_run import (
    AgentMode,
    AgentRun,
    AgentTask,
    CapabilityProfile,
    RunStatus,
    RunUsage,
)
from app.domain.models.research import ResearchSource
from app.interfaces.endpoints.session_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_agent_service


class FakeAgentService:
    def __init__(self) -> None:
        self.run = AgentRun(
            id="run-1",
            session_id="session-1",
            mode=AgentMode.RESEARCH_TEAM,
            status=RunStatus.RUNNING,
            goal="research",
            usage=RunUsage(llm_calls=3, total_tokens=120),
        )
        self.task = AgentTask(
            id="task-1",
            run_id=self.run.id,
            plan_version=1,
            task_key="topic",
            description="topic",
            objective="topic",
            capability_profile=CapabilityProfile.RESEARCH_READONLY,
            acceptance_criteria=["evidence"],
        )
        self.source = ResearchSource(
            run_id=self.run.id,
            canonical_url="https://example.com/",
            original_url="https://example.com/",
            title="Example",
            domain="example.com",
            content_type="text/html",
            content_hash="hash",
            object_storage_key="research/run-1/hash",
        )

    async def get_run(self, session_id, run_id):
        assert session_id == self.run.session_id
        assert run_id == self.run.id
        return self.run

    async def list_run_tasks(self, session_id, run_id):
        await self.get_run(session_id, run_id)
        return [self.task]

    async def list_run_sources(self, session_id, run_id):
        await self.get_run(session_id, run_id)
        return [self.source]

    async def cancel_run(self, session_id, run_id):
        await self.get_run(session_id, run_id)
        self.run.status = RunStatus.CANCELLED
        return self.run


def build_client():
    service = FakeAgentService()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_agent_service] = lambda: service
    return TestClient(app), service


def test_get_run_returns_budget_usage_and_status() -> None:
    client, service = build_client()

    response = client.get(
        f"/api/sessions/{service.run.session_id}/runs/{service.run.id}"
    )

    assert response.status_code == 200
    assert response.json()["data"]["usage"]["llm_calls"] == 3
    assert response.json()["data"]["status"] == "running"


def test_list_tasks_and_sources_excludes_snapshot_storage_key() -> None:
    client, service = build_client()

    tasks = client.get(
        f"/api/sessions/{service.run.session_id}/runs/{service.run.id}/tasks"
    )
    sources = client.get(
        f"/api/sessions/{service.run.session_id}/runs/{service.run.id}/sources"
    )

    assert tasks.status_code == sources.status_code == 200
    assert tasks.json()["data"][0]["task_key"] == "topic"
    assert sources.json()["data"][0]["domain"] == "example.com"
    assert "object_storage_key" not in sources.text


def test_cancel_is_idempotent() -> None:
    client, service = build_client()
    url = f"/api/sessions/{service.run.session_id}/runs/{service.run.id}/cancel"

    first = client.post(url)
    second = client.post(url)

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["status"] == "cancelled"
    assert second.json()["data"]["status"] == "cancelled"
