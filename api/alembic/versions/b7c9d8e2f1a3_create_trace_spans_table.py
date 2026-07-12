"""create trace spans table

Revision ID: b7c9d8e2f1a3
Revises: 9a4f6c2e1d70
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c9d8e2f1a3"
down_revision: Union[str, Sequence[str], None] = "9a4f6c2e1d70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trace_spans",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("parent_span_id", sa.String(length=255), nullable=True),
        sa.Column("span_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trace_spans_id"),
    )
    op.create_index(
        "ix_trace_spans_session_started",
        "trace_spans",
        ["session_id", "started_at"],
    )
    op.create_index(
        "ix_trace_spans_session_trace",
        "trace_spans",
        ["session_id", "trace_id"],
    )
    op.create_index(
        "ix_trace_spans_trace_parent",
        "trace_spans",
        ["trace_id", "parent_span_id"],
    )
    op.create_index("ix_trace_spans_span_type", "trace_spans", ["span_type"])
    op.create_index("ix_trace_spans_status", "trace_spans", ["status"])


def downgrade() -> None:
    op.drop_index("ix_trace_spans_status", table_name="trace_spans")
    op.drop_index("ix_trace_spans_span_type", table_name="trace_spans")
    op.drop_index("ix_trace_spans_trace_parent", table_name="trace_spans")
    op.drop_index("ix_trace_spans_session_trace", table_name="trace_spans")
    op.drop_index("ix_trace_spans_session_started", table_name="trace_spans")
    op.drop_table("trace_spans")
