"""Add pgvector extension and notes embedding foundation.

Revision ID: 20260904_03
Revises: 20260903_02
Create Date: 2026-09-04

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "20260904_03"
down_revision = "20260903_02"
branch_labels = None
depends_on = None

NOTE_EMBEDDING_DIMENSION = 1536
NOTES_EMBEDDING_INDEX_NAME = "ix_notes_embedding_hnsw_cosine"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "notes",
        sa.Column("embedding", Vector(NOTE_EMBEDDING_DIMENSION), nullable=True),
    )

    op.execute(
        f"CREATE INDEX {NOTES_EMBEDDING_INDEX_NAME} "
        "ON notes USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {NOTES_EMBEDDING_INDEX_NAME}")
    op.drop_column("notes", "embedding")

    bind = op.get_bind()
    remaining_vector_columns = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE udt_name = 'vector'
            """
        )
    ).scalar_one()

    if remaining_vector_columns == 0:
        op.execute("DROP EXTENSION IF EXISTS vector")
