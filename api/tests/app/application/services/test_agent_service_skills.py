import asyncio

from app.application.services.agent_service import AgentService
from app.domain.models.app_config import A2AConfig, AgentConfig, MCPConfig
from app.domain.models.session import Session
from app.domain.models.skill import SkillSnapshot


class FakeSessionRepository:
    async def save(self, session: Session) -> None:
        return None


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.session = FakeSessionRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FakeSandbox:
    id = "sandbox-id"

    @classmethod
    async def get(cls, sandbox_id: str):
        return cls()

    async def get_browser(self):
        return object()


class FakeTask:
    id = "task-id"
    created_runner = None

    @classmethod
    def create(cls, task_runner):
        cls.created_runner = task_runner
        return cls()


class FakeRegistry:
    def __init__(self, snapshot: tuple[SkillSnapshot, ...]) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def create_enabled_snapshot(self) -> tuple[SkillSnapshot, ...]:
        self.calls += 1
        return self.snapshot


class FakeRunner:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_new_task_receives_one_registry_snapshot(monkeypatch) -> None:
    async def scenario() -> None:
        snapshot = (
            SkillSnapshot(
                id="skill-id",
                name="demo",
                description="demo",
                skill_md="FULL BODY",
                root_path="demo",
                bundle_bytes=b"zip",
            ),
        )
        registry = FakeRegistry(snapshot)
        monkeypatch.setattr(
            "app.application.services.agent_service.AgentTaskRunner",
            FakeRunner,
        )
        service = AgentService(
            uow_factory=FakeUnitOfWork,
            llm=object(),
            agent_config=AgentConfig(),
            mcp_config=MCPConfig(),
            a2a_config=A2AConfig(),
            sandbox_cls=FakeSandbox,
            task_cls=FakeTask,
            json_parser=object(),
            search_engine=object(),
            file_storage=object(),
            skill_registry=registry,
        )
        session = Session(id="session-id", sandbox_id="sandbox-id")

        await service._create_task(session)

        assert registry.calls == 1
        assert FakeTask.created_runner.kwargs["skill_snapshots"] == snapshot

    asyncio.run(scenario())
