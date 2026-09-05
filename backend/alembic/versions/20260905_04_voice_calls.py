"""Add voice_calls table for V1.6 voice session mapping.

Revision ID: 20260905_04
Revises: 20260904_03
Create Date: 2026-09-05

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260905_04"
down_revision = "20260904_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_call_id", sa.String(length=255), nullable=False),
        sa.Column("agent_session_id", sa.BigInteger(), nullable=True),
        sa.Column("caller_identifier", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_session_id"], ["agent_sessions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("provider_call_id", name="uq_voice_calls_provider_call_id"),
    )

    op.create_index("ix_voice_calls_provider_call_id", "voice_calls", ["provider_call_id"])
    op.create_index("ix_voice_calls_started_at", "voice_calls", ["started_at"])
    op.create_index("ix_voice_calls_status", "voice_calls", ["status"])


def downgrade() -> None:
    op.drop_index("ix_voice_calls_status", table_name="voice_calls")
    op.drop_index("ix_voice_calls_started_at", table_name="voice_calls")
    op.drop_index("ix_voice_calls_provider_call_id", table_name="voice_calls")
    op.drop_table("voice_calls")
