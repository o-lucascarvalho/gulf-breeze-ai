from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agents.agent import AgentOrchestrator
from app.agents.types import ModelResponse, ModelToolCall
from app.core.config import get_settings
from app.db.models import AgentAction, AgentSession, Customer, Invoice, InvoiceItem, Job, Note
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


class AgentIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_name = f"gulf_breeze_air_v140_test_{uuid.uuid4().hex[:8]}"
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

        now = datetime.now(timezone.utc)
        vector = [0.0] * NOTE_EMBEDDING_DIMENSION
        vector[0] = 1.0

        with self.Session() as session:
            session.add(Customer(id="cus_1", first_name="Robert", last_name="Smith", company="Smith Home"))
            session.add(
                Job(
                    id="job_1",
                    customer_id="cus_1",
                    description="Compressor issue",
                    work_status="completed",
                    scheduled_start=now - timedelta(days=2),
                    scheduled_end=now - timedelta(days=2, hours=-1),
                    outstanding_balance=Decimal("55.00"),
                )
            )
            session.add(
                Invoice(
                    id="inv_1",
                    job_id="job_1",
                    invoice_number="INV-123",
                    status="open",
                    subtotal=Decimal("250.00"),
                    amount=Decimal("300.00"),
                    due_amount=Decimal("55.00"),
                )
            )
            session.add(
                InvoiceItem(
                    id="item_1",
                    invoice_id="inv_1",
                    job_id="job_1",
                    name="Compressor",
                    type="part",
                    unit_price=Decimal("200.00"),
                    quantity=Decimal("1.00"),
                    amount=Decimal("200.00"),
                )
            )
            session.add(
                Note(
                    id="nte_1",
                    job_id="job_1",
                    note_index=1,
                    content="Technician identified compressor failure and replaced capacitor.",
                    embedding=vector,
                )
            )
            session.commit()

        semantic_retriever = PgVectorSemanticRetriever(
            self.Session,
            FakeEmbeddingProvider(),
            expected_dimension=NOTE_EMBEDDING_DIMENSION,
            timeout=1.0,
            max_top_k=50,
        )
        self.registry = build_default_tool_registry(self.Session, semantic_retriever)

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

    def test_end_to_end_orchestration_with_real_tools_and_audit_tables(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="1", name="search_customers", arguments={"name": "Robert Smith"})],
            ),
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="2", name="get_customer_balance", arguments={"customer_id": "cus_1"})],
            ),
            ModelResponse(
                message=None,
                tool_calls=[
                    ModelToolCall(
                        call_id="3",
                        name="semantic_search_notes",
                        arguments={"query": "compressor failure", "customer_id": "cus_1", "top_k": 3},
                    )
                ],
            ),
            ModelResponse(message="Robert Smith has an outstanding balance of 55.00 and notes mention compressor failure."),
        ]

        agent = AgentOrchestrator(
            model=FakeModel(scripted),
            registry=self.registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )

        result = agent.run("How much does Robert owe and did he have compressor issues?", channel="cli")
        self.assertEqual(result.status, "completed")
        self.assertFalse(result.needs_handoff)
        self.assertEqual(len(result.tool_calls), 3)

        with self.Session() as session:
            agent_session = session.scalar(select(AgentSession).where(AgentSession.id == result.session_id))
            self.assertIsNotNone(agent_session)
            self.assertEqual(agent_session.status, "completed")
            self.assertEqual(agent_session.channel, "cli")

            actions = list(session.scalars(select(AgentAction).where(AgentAction.session_id == result.session_id)))
            self.assertEqual(len(actions), 3)
            self.assertEqual({row.action_type for row in actions}, {"search_customers", "get_customer_balance", "semantic_search_notes"})


if __name__ == "__main__":
    unittest.main()
