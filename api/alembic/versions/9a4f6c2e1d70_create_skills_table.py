"""create skills table

Revision ID: 9a4f6c2e1d70
Revises: 0e0d242438bc
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9a4f6c2e1d70"
down_revision: Union[str, Sequence[str], None] = "0e0d242438bc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("skill_md", sa.Text(), nullable=False),
        sa.Column("root_path", sa.String(length=1024), nullable=False),
        sa.Column("bundle_key", sa.String(length=1024), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP(0)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skills_id"),
        sa.UniqueConstraint("name", name="uq_skills_name"),
    )


def downgrade() -> None:
    op.drop_table("skills")
