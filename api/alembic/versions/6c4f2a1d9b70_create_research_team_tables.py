"""create research team tables

Revision ID: 6c4f2a1d9b70
Revises: 0e0d242438bc
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6c4f2a1d9b70"
down_revision: Union[str, Sequence[str], None] = "0e0d242438bc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("budget_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs_id"),
    )
    op.create_index(
        "uq_agent_runs_active_research_session",
        "agent_runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text(
            "mode = 'research_team' AND status IN "
            "('pending','planning','running','reviewing','synthesizing')"
        ),
    )

    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("task_key", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("capability_profile", sa.String(length=64), nullable=False),
        sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("source_requirements", postgresql.JSONB(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assigned_agent_id", sa.String(length=255), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_agent_tasks_id"),
        sa.UniqueConstraint(
            "run_id",
            "plan_version",
            "task_key",
            name="uq_agent_tasks_run_plan_key",
        ),
    )
    op.create_index("ix_agent_tasks_run_id", "agent_tasks", ["run_id"])

    op.create_table(
        "agent_task_dependencies",
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("depends_on_task_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["agent_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_task_id"],
            ["agent_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "task_id",
            "depends_on_task_id",
            name="pk_agent_task_dependencies",
        ),
    )

    op.create_table(
        "agent_task_attempts",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("agent_profile", sa.String(length=64), nullable=False),
        sa.Column("model_profile", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_agent_task_attempts_id"),
        sa.UniqueConstraint(
            "run_id",
            "task_id",
            "attempt_number",
            name="uq_agent_task_attempts_run_task_number",
        ),
    )

    op.create_table(
        "research_sources",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("object_storage_key", sa.Text(), nullable=False),
        sa.Column("source_class", sa.String(length=32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_research_sources_id"),
        sa.UniqueConstraint(
            "run_id",
            "content_hash",
            name="uq_research_sources_run_content_hash",
        ),
    )
    op.create_index(
        "ix_research_sources_run_url",
        "research_sources",
        ["run_id", "canonical_url"],
    )

    op.create_table(
        "evidence_excerpts",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("excerpt_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["research_sources.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_excerpts_id"),
        sa.UniqueConstraint(
            "source_id",
            "excerpt_hash",
            name="uq_evidence_excerpts_source_hash",
        ),
    )

    op.create_table(
        "research_claims",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("caveats", postgresql.JSONB(), nullable=False),
        sa.Column("support_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_research_claims_id"),
    )

    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.String(length=255), nullable=False),
        sa.Column("evidence_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["research_claims.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_excerpts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("claim_id", "evidence_id", name="pk_claim_evidence"),
    )


def downgrade() -> None:
    op.drop_table("claim_evidence")
    op.drop_table("research_claims")
    op.drop_table("evidence_excerpts")
    op.drop_index("ix_research_sources_run_url", table_name="research_sources")
    op.drop_table("research_sources")
    op.drop_table("agent_task_attempts")
    op.drop_table("agent_task_dependencies")
    op.drop_index("ix_agent_tasks_run_id", table_name="agent_tasks")
    op.drop_table("agent_tasks")
    op.drop_index(
        "uq_agent_runs_active_research_session",
        table_name="agent_runs",
    )
    op.drop_table("agent_runs")
