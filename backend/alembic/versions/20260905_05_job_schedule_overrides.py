"""Add job_schedule_overrides table for operational schedule mutations.

Revision ID: 20260905_05
Revises: 20260905_04
Create Date: 2026-09-05

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260905_05"
down_revision = "20260905_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_schedule_overrides",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("original_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("new_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_agent_session_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_agent_session_id"], ["agent_sessions.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'cancelled')",
            name="ck_job_schedule_overrides_status_valid",
        ),
    )

    op.create_index("ix_job_schedule_overrides_job_id", "job_schedule_overrides", ["job_id"])
    op.create_index("ix_job_schedule_overrides_status", "job_schedule_overrides", ["status"])
    op.create_index("ix_job_schedule_overrides_created_at", "job_schedule_overrides", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_job_schedule_overrides_created_at", table_name="job_schedule_overrides")
    op.drop_index("ix_job_schedule_overrides_status", table_name="job_schedule_overrides")
    op.drop_index("ix_job_schedule_overrides_job_id", table_name="job_schedule_overrides")
    op.drop_table("job_schedule_overrides")
