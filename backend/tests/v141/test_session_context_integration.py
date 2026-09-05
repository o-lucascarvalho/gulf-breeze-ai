from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agents.agent import AgentOrchestrator
from app.agents.types import ModelResponse, ModelToolCall
from app.core.config import get_settings
from app.db.models import AgentAction, AgentSession, Customer, Job, Note
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


class SessionContextIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_name = f"gulf_breeze_air_v141_test_{uuid.uuid4().hex[:8]}"
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
            session.add(Job(id="job_1", customer_id="cus_tamara", description="Cooling issue", scheduled_start=datetime.now(timezone.utc)))
            session.add(
                Note(
                    id="nte_1",
                    job_id="job_1",
                    note_index=1,
                    content="possible leak noted, could not locate leak",
                    embedding=vector,
                )
            )
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

    def test_note_reference_follow_up_resolves_previous_first_result(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        "2",
                        "semantic_search_notes",
                        {"query": "refrigerant leak", "customer_id": "cus_tamara", "top_k": 3},
                    )
                ],
                message=None,
            ),
            ModelResponse(message="1. possible leak noted and location not confirmed."),
            ModelResponse(tool_calls=[ModelToolCall("3", "get_job", {})], message=None),
            ModelResponse(message="For note 1, the visit was job_1 and details are available there."),
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

        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(second.active_customer_id, "cus_tamara")
        self.assertEqual(second.active_job_id, "job_1")
        self.assertIn("nte_1", second.referenced_note_ids)

        with self.Session() as session:
            db_session = session.scalar(select(AgentSession).where(AgentSession.id == first.session_id))
            self.assertEqual(db_session.status, "active")
            metadata = db_session.metadata_json or {}
            context = metadata.get("conversation_context") or {}
            self.assertEqual(context.get("customer_id"), "cus_tamara")
            self.assertEqual(context.get("last_job_id"), "job_1")

            actions = list(session.scalars(select(AgentAction).where(AgentAction.session_id == first.session_id)))
            self.assertTrue(any(row.action_type == "semantic_search_notes" for row in actions))
            get_job_actions = [row for row in actions if row.action_type == "get_job"]
            self.assertTrue(get_job_actions)
            self.assertEqual(get_job_actions[-1].arguments.get("job_id"), "job_1")


if __name__ == "__main__":
    unittest.main()
