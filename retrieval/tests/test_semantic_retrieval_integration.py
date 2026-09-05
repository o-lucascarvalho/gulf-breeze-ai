from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from retrieval.backend import ensure_backend_path
from retrieval.exceptions import SemanticQueryValidationError
from retrieval.repository import NotesSemanticRepository
from retrieval.service import MAX_TOP_K, PgVectorSemanticRetriever

ensure_backend_path()

from app.core.config import get_settings  # noqa: E402
from app.db.models import Customer, Job, Note  # noqa: E402
from app.db.vector_settings import NOTE_EMBEDDING_DIMENSION  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


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


def _make_vector(x0: float, x1: float = 0.0) -> list[float]:
    vector = [0.0] * NOTE_EMBEDDING_DIMENSION
    vector[0] = x0
    vector[1] = x1
    return vector


@dataclass
class MappingEmbeddingProvider:
    query_to_vector: dict[str, list[float]]
    calls: int = 0

    model_name: str = "fake"

    def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        self.calls += 1
        return [self.query_to_vector[text] for text in texts]


class SemanticRetrievalIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_name = f"gulf_breeze_air_v132_test_{uuid.uuid4().hex[:8]}"

        try:
            with psycopg.connect(_admin_dsn("postgres"), autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(f'CREATE DATABASE "{self.db_name}"')

            _run_alembic(self.db_name, "upgrade", "head")
        except Exception as exc:  # pragma: no cover - skip path for unavailable local PG setup
            self.skipTest(f"PostgreSQL integration prerequisites unavailable: {exc}")

        dsn = _admin_dsn(self.db_name).replace("postgresql://", "postgresql+psycopg://")
        self.engine = create_engine(dsn)
        self.Session = sessionmaker(bind=self.engine)

        with self.Session() as session:
            session.add_all(
                [
                    Customer(id="cus_1"),
                    Customer(id="cus_2"),
                    Job(id="job_1", customer_id="cus_1", description="job 1"),
                    Job(id="job_2", customer_id="cus_1", description="job 2"),
                    Job(id="job_3", customer_id="cus_2", description="job 3"),
                ]
            )
            session.flush()

            session.add_all(
                [
                    Note(
                        id="n_a",
                        job_id="job_1",
                        note_index=1,
                        content="Technician found compressor failure.",
                        embedding=_make_vector(1.0, 0.0),
                    ),
                    Note(
                        id="n_b",
                        job_id="job_2",
                        note_index=1,
                        content="Previous refrigerant leak reported.",
                        embedding=_make_vector(0.8, 0.2),
                    ),
                    Note(
                        id="n_c",
                        job_id="job_3",
                        note_index=1,
                        content="Customer said the unit was not cooling.",
                        embedding=_make_vector(0.0, 1.0),
                    ),
                    Note(
                        id="n_tie_a",
                        job_id="job_1",
                        note_index=2,
                        content="Tie candidate A.",
                        embedding=_make_vector(0.6, 0.8),
                    ),
                    Note(
                        id="n_tie_b",
                        job_id="job_2",
                        note_index=2,
                        content="Tie candidate B.",
                        embedding=_make_vector(0.6, 0.8),
                    ),
                    Note(
                        id="n_neg",
                        job_id="job_3",
                        note_index=2,
                        content="Opposite direction.",
                        embedding=_make_vector(-1.0, 0.0),
                    ),
                    Note(
                        id="n_null",
                        job_id="job_3",
                        note_index=3,
                        content="No embedding should not be returned.",
                        embedding=None,
                    ),
                ]
            )
            session.commit()

        self.provider = MappingEmbeddingProvider(
            query_to_vector={
                "previous refrigerant leak": _make_vector(1.0, 0.0),
                "previous ac issues": _make_vector(1.0, 0.0),
            }
        )
        self.retriever = PgVectorSemanticRetriever(
            self.Session,
            self.provider,
            expected_dimension=NOTE_EMBEDDING_DIMENSION,
            timeout=5.0,
            repository=NotesSemanticRepository(),
        )

    def tearDown(self) -> None:
        if hasattr(self, "engine"):
            self.engine.dispose()

        try:
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
        except DBAPIError:
            pass

    def test_cosine_ranking_is_descending_and_deterministic(self) -> None:
        results = self.retriever.search("previous refrigerant leak", top_k=10)

        self.assertEqual(self.provider.calls, 1)
        self.assertLessEqual(len(results), 10)

        ids = [row.note_id for row in results]
        self.assertEqual(ids[0], "n_a")
        self.assertEqual(ids[1], "n_b")

        tie_index_a = ids.index("n_tie_a")
        tie_index_b = ids.index("n_tie_b")
        self.assertLess(tie_index_a, tie_index_b)

        self.assertNotIn("n_null", ids)

        similarities = [row.similarity_score for row in results]
        self.assertEqual(similarities, sorted(similarities, reverse=True))

    def test_top_k_respected(self) -> None:
        for top_k in (1, 5, 10):
            results = self.retriever.search("previous refrigerant leak", top_k=top_k)
            self.assertLessEqual(len(results), top_k)

    def test_max_top_k_allowed(self) -> None:
        results = self.retriever.search("previous refrigerant leak", top_k=MAX_TOP_K)
        self.assertLessEqual(len(results), MAX_TOP_K)

    def test_customer_filter(self) -> None:
        results = self.retriever.search("previous ac issues", top_k=10, customer_id="cus_1")
        self.assertTrue(results)
        self.assertTrue(all(row.customer_id == "cus_1" for row in results))

    def test_job_filter(self) -> None:
        results = self.retriever.search("previous ac issues", top_k=10, job_id="job_3")
        self.assertTrue(results)
        self.assertTrue(all(row.job_id == "job_3" for row in results))

    def test_combined_filters(self) -> None:
        results = self.retriever.search(
            "previous ac issues",
            top_k=10,
            customer_id="cus_1",
            job_id="job_2",
        )
        self.assertTrue(results)
        self.assertTrue(all(row.customer_id == "cus_1" and row.job_id == "job_2" for row in results))

    def test_empty_result_returns_empty_list(self) -> None:
        results = self.retriever.search("previous ac issues", top_k=10, customer_id="cus_unknown")
        self.assertEqual(results, [])

    def test_invalid_top_k_rejected(self) -> None:
        with self.assertRaises(SemanticQueryValidationError):
            self.retriever.search("previous ac issues", top_k=0)


if __name__ == "__main__":
    unittest.main()
