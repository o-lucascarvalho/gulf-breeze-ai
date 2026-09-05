from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agents.agent import AgentOrchestrator
from app.agents.types import ModelResponse, ModelToolCall
from app.core.config import get_settings
from app.db.models import AgentAction, Customer, Job, Note
from app.db.vector_settings import NOTE_EMBEDDING_DIMENSION
from app.services.agent_actions import AgentActionService
from app.services.agent_sessions import AgentSessionService
from app.tools.factory import build_default_tool_registry
from retrieval.service import PgVectorSemanticRetriever

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"


class FakeModel:
    def __init__(self, scripted: list[ModelResponse]) -> None:
        self._scripted = scripted
        self._index = 0

    def run(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        response = self._scripted[self._index]
        self._index += 1
        return response


@dataclass
class FakeEmbeddingProvider:
    model_name: str = "fake"

    def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        vector = [0.0] * NOTE_EMBEDDING_DIMENSION
        vector[0] = 1.0
        return [vector for _ in texts]


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


class DeterministicNoteFollowupIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_name = f"gulf_breeze_air_v142_test_{uuid.uuid4().hex[:8]}"
        try:
            with psycopg.connect(_admin_dsn("postgres"), autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(f'CREATE DATABASE "{self.db_name}"')
            _run_alembic(self.db_name, "upgrade", "head")
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"PostgreSQL integration prerequisites unavailable: {exc}")

        dsn = _admin_dsn(self.db_name).replace("postgresql://", "postgresql+psycopg://")
        self.engine = create_engine(dsn)
        self.Session = sessionmaker(bind=self.engine)

        vector = [0.0] * NOTE_EMBEDDING_DIMENSION
        vector[0] = 1.0

        with self.Session() as session:
            session.add(Customer(id="cus_tamara", first_name="Tamara", last_name="Cruz"))
            session.add(Job(id="job_1", customer_id="cus_tamara", description="Leak call"))
            session.add(Job(id="job_2", customer_id="cus_tamara", description="Cooling call"))
            session.add(Note(id="nte_a", job_id="job_1", note_index=1, content="possible leak, not located", embedding=vector))
            session.add(Note(id="nte_b", job_id="job_2", note_index=1, content="cooling complaint", embedding=vector))
            session.commit()

        semantic = PgVectorSemanticRetriever(
            self.Session,
            FakeEmbeddingProvider(),
            expected_dimension=NOTE_EMBEDDING_DIMENSION,
            timeout=1.0,
            max_top_k=50,
        )
        self.registry = build_default_tool_registry(self.Session, semantic)

    def tearDown(self) -> None:
        if hasattr(self, "engine"):
            self.engine.dispose()

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

    def test_followup_note_reference_uses_get_note_without_semantic_rerun(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant leak", "customer_id": "cus_tamara", "top_k": 2})], message=None),
            ModelResponse(message="1. possible leak\n2. cooling complaint"),
            ModelResponse(tool_calls=[ModelToolCall("3", "semantic_search_notes", {"query": "details about note 1"})], message=None),
            ModelResponse(message="Here are details for the requested note."),
        ]

        agent = AgentOrchestrator(
            model=FakeModel(scripted),
            registry=self.registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )

        first = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)
        second = agent.run("Tell me more about note 1.", session_id=first.session_id, finalize_session_on_return=False)

        with self.Session() as session:
            actions = list(
                session.scalars(
                    select(AgentAction)
                    .where(AgentAction.session_id == first.session_id)
                    .order_by(AgentAction.id.asc())
                )
            )

        semantic_calls = [a for a in actions if a.action_type == "semantic_search_notes"]
        get_note_calls = [a for a in actions if a.action_type == "get_note"]

        self.assertEqual(len(semantic_calls), 1)
        self.assertEqual(len(get_note_calls), 1)
        self.assertEqual(get_note_calls[0].arguments.get("note_id"), second.referenced_note_ids[0])
        self.assertEqual(second.active_job_id, "job_1")


if __name__ == "__main__":
    unittest.main()
