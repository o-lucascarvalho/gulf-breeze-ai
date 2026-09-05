from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import desc, select

from app.agents.agent import AgentOrchestrator, build_default_agent
from app.agents.context import ConversationContext
from app.agents.types import AgentResult
from app.db.models import AgentAction, AgentSession
from app.db.session import SessionLocal


@dataclass(frozen=True)
class AgentServiceError(Exception):
    code: str
    message: str
    status_code: int


class AgentService:
    def __init__(self, *, agent: AgentOrchestrator, session_factory=SessionLocal) -> None:
        self._agent = agent
        self._session_factory = session_factory

    def create_session(self, *, caller_identifier: str | None = None, channel: str = "api") -> dict[str, Any]:
        session_id = self._agent.session_service.create_session(
            channel=channel,
            caller_identifier=caller_identifier,
            metadata={"tool_round_limit": self._agent.max_tool_rounds},
        )
        session = self._get_session_record(session_id)
        if session is None:
            raise AgentServiceError(
                code="session_creation_failed",
                message="The agent session could not be created.",
                status_code=500,
            )
        return self._serialize_session(session)

    def chat(self, *, message: str, session_id: int | None, caller_identifier: str | None, debug: bool) -> dict[str, Any]:
        if session_id is not None:
            session = self._get_session_record(session_id)
            if session is None:
                raise AgentServiceError(
                    code="session_not_found",
                    message="The requested agent session was not found.",
                    status_code=404,
                )
            if session.status != "active":
                raise AgentServiceError(
                    code="session_closed",
                    message="The requested agent session is no longer active.",
                    status_code=409,
                )

        result = self._agent.run(
            message,
            session_id=session_id,
            channel="api",
            caller_identifier=caller_identifier,
            finalize_session_on_return=False,
        )
        return self._serialize_chat_result(result, debug=debug)

    def close_session(self, session_id: int) -> dict[str, Any]:
        session = self._get_session_record(session_id)
        if session is None:
            raise AgentServiceError(
                code="session_not_found",
                message="The requested agent session was not found.",
                status_code=404,
            )
        if session.status != "active":
            raise AgentServiceError(
                code="session_closed",
                message="The requested agent session is no longer active.",
                status_code=409,
            )

        self._agent.close_session(session_id)
        return {"session_id": session_id, "status": "completed"}

    def get_session(self, session_id: int) -> dict[str, Any]:
        session = self._get_session_record(session_id)
        if session is None:
            raise AgentServiceError(
                code="session_not_found",
                message="The requested agent session was not found.",
                status_code=404,
            )
        return self._serialize_session(session)

    def list_sessions(self, *, limit: int, status: str | None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(AgentSession)
            if status:
                statement = statement.where(AgentSession.status == status)
            statement = statement.order_by(desc(AgentSession.started_at), desc(AgentSession.id)).limit(limit)
            rows = session.scalars(statement).all()
        return [self._serialize_session(row) for row in rows]

    def list_actions(self, *, session_id: int, limit: int) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            session_exists = session.get(AgentSession, session_id)
            if session_exists is None:
                raise AgentServiceError(
                    code="session_not_found",
                    message="The requested agent session was not found.",
                    status_code=404,
                )

            statement = (
                select(AgentAction)
                .where(AgentAction.session_id == session_id)
                .order_by(AgentAction.timestamp.asc(), AgentAction.id.asc())
                .limit(limit)
            )
            rows = session.scalars(statement).all()

        return [
            {
                "id": row.id,
                "timestamp": row.timestamp,
                "action_type": row.action_type,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "status": row.status,
                "latency_ms": row.latency_ms,
                "arguments": row.arguments or {},
                "result": row.result,
            }
            for row in rows
        ]

    def _get_session_record(self, session_id: int) -> AgentSession | None:
        with self._session_factory() as session:
            return session.get(AgentSession, session_id)

    @staticmethod
    def _serialize_chat_result(result: AgentResult, *, debug: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": result.session_id,
            "response": result.response,
            "status": result.status,
            "needs_handoff": result.needs_handoff,
            "handoff_reason": result.handoff_reason,
        }

        if debug:
            payload.update(
                {
                    "tool_calls": [asdict(call) for call in result.tool_calls],
                    "active_customer_id": result.active_customer_id,
                    "active_job_id": result.active_job_id,
                    "active_invoice_id": result.active_invoice_id,
                    "referenced_note_ids": result.referenced_note_ids,
                }
            )

        return payload

    @staticmethod
    def _serialize_session(record: AgentSession) -> dict[str, Any]:
        context = ConversationContext.from_metadata(record.metadata_json)
        metadata = record.metadata_json or {}

        return {
            "session_id": record.id,
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "channel": record.channel,
            "status": record.status,
            "caller_identifier": record.caller_identifier,
            "customer_id": record.customer_id,
            "customer_display_name": context.customer_display_name,
            "context_summary": {
                "context_version": context.context_version,
                "customer_id": context.customer_id,
                "customer_display_name": context.customer_display_name,
                "last_job_id": context.last_job_id,
                "last_invoice_id": context.last_invoice_id,
                "message_count": len(context.messages),
                "recent_note_references": [
                    {
                        "position": row.position,
                        "note_id": row.note_id,
                        "job_id": row.job_id,
                        "customer_id": row.customer_id,
                    }
                    for row in context.recent_note_references
                ],
                "recent_job_references": [
                    {
                        "position": row.position,
                        "job_id": row.job_id,
                        "customer_id": row.customer_id,
                        "description": row.description,
                    }
                    for row in context.recent_job_references
                ],
            },
            "metadata": {
                key: value
                for key, value in metadata.items()
                if key != "conversation_context"
            },
        }


from functools import lru_cache


@lru_cache(maxsize=1)
def build_default_agent_service() -> AgentService:
    return AgentService(agent=build_default_agent(), session_factory=SessionLocal)