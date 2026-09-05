from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

import psycopg
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.db.vector_settings import NOTE_EMBEDDING_DIMENSION  # noqa: E402


def _alembic_cmd() -> str:
    return str(Path(sys.executable).parent / "alembic")


def _admin_dsn(db_name: str) -> str:
    settings = get_settings()
    return (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}@"
        f"{settings.postgres_host}:{settings.postgres_port}/{db_name}"
    )


def _run_alembic(database_name: str, command: str, revision: str) -> None:
    env = os.environ.copy()
    env["POSTGRES_DB"] = database_name

    subprocess.run(
        [
            _alembic_cmd(),
            "-c",
            str(BACKEND_DIR / "alembic.ini"),
            command,
            revision,
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _vector_literal(length: int) -> str:
    return "[" + ",".join(["0"] * length) + "]"


class VectorMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_name = f"gulf_breeze_air_v130_test_{uuid.uuid4().hex[:8]}"

        with psycopg.connect(_admin_dsn("postgres"), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{self.db_name}"')

    def tearDown(self) -> None:
        with psycopg.connect(_admin_dsn("postgres"), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s AND pid <> pg_backend_pid()
                    """,
                    (self.db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{self.db_name}"')

    def test_upgrade_and_downgrade_vector_foundation(self) -> None:
        _run_alembic(self.db_name, "upgrade", "20260903_02")

        engine = create_engine(_admin_dsn(self.db_name).replace("postgresql://", "postgresql+psycopg://"))
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO customers (id, first_name)
                    VALUES ('cus_test', 'Test')
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (id, customer_id, description)
                    VALUES ('job_test', 'cus_test', 'Test job')
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO notes (id, job_id, note_index, content)
                    VALUES ('nte_test', 'job_test', 1, 'note before vector migration')
                    """
                )
            )

        with engine.connect() as conn:
            pre_note_count = conn.execute(text("SELECT count(*) FROM notes")).scalar_one()
            pre_note_content = conn.execute(text("SELECT content FROM notes WHERE id = 'nte_test'")).scalar_one()

        _run_alembic(self.db_name, "upgrade", "head")

        insp = inspect(engine)
        note_columns = {col["name"] for col in insp.get_columns("notes")}
        self.assertIn("embedding", note_columns)

        note_fks = insp.get_foreign_keys("notes")
        self.assertTrue(any(fk.get("referred_table") == "jobs" for fk in note_fks))

        with engine.connect() as conn:
            extension_exists = conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            ).scalar_one()
            self.assertTrue(extension_exists)

            vector_type = conn.execute(
                text(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod)
                    FROM pg_attribute a
                    JOIN pg_class c ON c.oid = a.attrelid
                    WHERE c.relname = 'notes'
                      AND a.attname = 'embedding'
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    """
                )
            ).scalar_one()
            self.assertEqual(vector_type, f"vector({NOTE_EMBEDDING_DIMENSION})")

            index_rows = conn.execute(
                text(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'notes'
                      AND indexname = 'ix_notes_embedding_hnsw_cosine'
                    """
                )
            ).all()
            self.assertEqual(len(index_rows), 1)
            indexdef = index_rows[0][0]
            self.assertIn("USING hnsw", indexdef)
            self.assertIn("vector_cosine_ops", indexdef)

            post_note_count = conn.execute(text("SELECT count(*) FROM notes")).scalar_one()
            post_note_content = conn.execute(text("SELECT content FROM notes WHERE id = 'nte_test'")).scalar_one()
            self.assertEqual(pre_note_count, post_note_count)
            self.assertEqual(pre_note_content, post_note_content)

            conn.execute(
                text("UPDATE notes SET embedding = CAST(:embedding AS vector) WHERE id = 'nte_test'"),
                {"embedding": _vector_literal(NOTE_EMBEDDING_DIMENSION)},
            )

            with self.assertRaises(DBAPIError):
                conn.execute(
                    text("UPDATE notes SET embedding = CAST(:embedding AS vector) WHERE id = 'nte_test'"),
                    {"embedding": _vector_literal(3)},
                )

        _run_alembic(self.db_name, "downgrade", "20260903_02")

        insp_after = inspect(engine)
        note_columns_after = {col["name"]: str(col["type"]) for col in insp_after.get_columns("notes")}
        self.assertNotIn("embedding", note_columns_after)

        with engine.connect() as conn:
            index_after = conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'notes'
                      AND indexname = 'ix_notes_embedding_hnsw_cosine'
                    """
                )
            ).scalar_one()
            self.assertEqual(index_after, 0)

            extension_after = conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            ).scalar_one()
            self.assertFalse(extension_after)

            downgraded_note_count = conn.execute(text("SELECT count(*) FROM notes")).scalar_one()
            downgraded_note_content = conn.execute(text("SELECT content FROM notes WHERE id = 'nte_test'")).scalar_one()
            self.assertEqual(downgraded_note_count, 1)
            self.assertEqual(downgraded_note_content, "note before vector migration")


if __name__ == "__main__":
    unittest.main()
