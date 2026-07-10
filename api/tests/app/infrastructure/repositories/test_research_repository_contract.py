from app.domain.models.agent_run import AgentMode, AgentRun
from app.domain.models.research import ResearchSource
from app.domain.repositories.agent_run_repository import AgentRunRepository
from app.domain.repositories.research_repository import ResearchRepository
from app.infrastructure.models.agent_run import AgentRunModel
from app.infrastructure.models.research import ResearchSourceModel
from app.infrastructure.repositories.db_agent_run_repository import DBAgentRunRepository
from app.infrastructure.repositories.db_research_repository import DBResearchRepository
from app.infrastructure.repositories.db_uow import DBUnitOfWork


def test_agent_run_model_round_trips_domain_values() -> None:
    run = AgentRun(
        id="run-1",
        session_id="session-1",
        mode=AgentMode.RESEARCH_TEAM,
        goal="research",
    )

    restored = AgentRunModel.from_domain(run).to_domain()

    assert restored == run


def test_source_model_round_trip_preserves_metadata() -> None:
    source = ResearchSource(
        id="source-1",
        run_id="run-1",
        canonical_url="https://example.com/article",
        original_url="https://example.com/article?ref=search",
        title="Example",
        domain="example.com",
        content_type="text/html",
        content_hash="hash",
        object_storage_key="sources/hash",
        metadata={"language": "en"},
    )

    restored = ResearchSourceModel.from_domain(source).to_domain()

    assert restored == source


def test_repository_protocols_expose_required_methods() -> None:
    run_methods = {
        "add",
        "get",
        "get_active_by_session",
        "update",
        "add_tasks",
        "list_tasks",
        "update_task",
        "add_attempt",
        "update_attempt",
        "mark_active_interrupted",
    }
    research_methods = {
        "add_source",
        "get_source",
        "list_sources",
        "add_evidence",
        "list_evidence",
        "add_claim",
        "update_claim",
        "list_claims",
    }

    assert run_methods.issubset(AgentRunRepository.__dict__)
    assert research_methods.issubset(ResearchRepository.__dict__)


async def test_uow_registers_research_repositories_per_session() -> None:
    class FakeSession:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

        async def close(self) -> None:
            return None

    session = FakeSession()
    uow = DBUnitOfWork(lambda: session)

    async with uow:
        assert isinstance(uow.agent_run, DBAgentRunRepository)
        assert isinstance(uow.research, DBResearchRepository)
        assert uow.agent_run.db_session is session
        assert uow.research.db_session is session
