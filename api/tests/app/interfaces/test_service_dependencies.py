from types import SimpleNamespace

import app.interfaces.service_dependencies as dependencies


class _AppConfigRepository:
    def __init__(self, config_path: str) -> None:
        self.config_path = config_path

    def load(self) -> SimpleNamespace:
        return SimpleNamespace(
            llm_config={"model": "test"},
            agent_config={},
            mcp_config={},
            a2a_config={},
        )


def _patch_common(monkeypatch, *, research_team_enabled: bool) -> None:
    monkeypatch.setattr(
        dependencies,
        "settings",
        SimpleNamespace(
            app_config_filepath="config.yaml",
            research_team_enabled=research_team_enabled,
        ),
    )
    monkeypatch.setattr(dependencies, "FileAppConfigRepository", _AppConfigRepository)
    monkeypatch.setattr(dependencies, "OpenAILLM", lambda config: ("llm", config))
    monkeypatch.setattr(dependencies, "OSSFileStorage", lambda **kwargs: ("files", kwargs))
    monkeypatch.setattr(dependencies, "RepairJSONParser", lambda: "json-parser")
    monkeypatch.setattr(dependencies, "BingSearchEngine", lambda: "search-engine")
    monkeypatch.setattr(dependencies, "AgentService", lambda **kwargs: kwargs)
    monkeypatch.setattr(dependencies, "FileService", lambda **kwargs: kwargs)


def test_agent_service_builds_shared_dependencies_with_feature_disabled(
    monkeypatch,
) -> None:
    _patch_common(monkeypatch, research_team_enabled=False)

    service = dependencies.get_agent_service(oss=SimpleNamespace(bucket="bucket"))

    assert service["json_parser"] == "json-parser"
    assert service["search_engine"] == "search-engine"
    assert service["research_flow_factory"] is None


def test_file_service_does_not_build_research_runtime(monkeypatch) -> None:
    _patch_common(monkeypatch, research_team_enabled=True)

    service = dependencies.get_file_service(oss=SimpleNamespace(bucket="bucket"))

    assert set(service) == {"uow_factory", "file_storage"}
