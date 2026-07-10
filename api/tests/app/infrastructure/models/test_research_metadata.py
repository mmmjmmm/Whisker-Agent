from sqlalchemy import UniqueConstraint

from app.infrastructure.models import Base


def test_research_tables_and_active_run_index_are_registered() -> None:
    expected = {
        "agent_runs",
        "agent_tasks",
        "agent_task_dependencies",
        "agent_task_attempts",
        "research_sources",
        "evidence_excerpts",
        "research_claims",
        "claim_evidence",
    }

    assert expected.issubset(Base.metadata.tables)
    indexes = {index.name for index in Base.metadata.tables["agent_runs"].indexes}
    assert "uq_agent_runs_active_research_session" in indexes


def test_attempt_and_source_uniqueness_are_registered() -> None:
    attempt_table = Base.metadata.tables["agent_task_attempts"]
    source_table = Base.metadata.tables["research_sources"]

    attempt_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in attempt_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    source_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in source_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("run_id", "task_id", "attempt_number") in attempt_unique_columns
    assert ("run_id", "content_hash") in source_unique_columns


def test_run_owned_foreign_keys_cascade_on_delete() -> None:
    for table_name in {
        "agent_tasks",
        "agent_task_attempts",
        "research_sources",
        "evidence_excerpts",
        "research_claims",
    }:
        table = Base.metadata.tables[table_name]
        run_foreign_keys = [
            key
            for key in table.foreign_keys
            if key.target_fullname == "agent_runs.id"
        ]
        assert run_foreign_keys
        assert all(key.ondelete == "CASCADE" for key in run_foreign_keys)

