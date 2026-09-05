from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import AgentSession


class AgentSessionService:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def create_session(
        self,
        *,
        channel: str,
        caller_identifier: str | None,
        metadata: dict | None,
    ) -> int:
        started_at = datetime.now(timezone.utc)
        with self._session_factory() as session:
            record = AgentSession(
                started_at=started_at,
                channel=channel,
                status="active",
                caller_identifier=caller_identifier,
                metadata_json=metadata,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return int(record.id)

    def get_session(self, session_id: int) -> AgentSession | None:
        with self._session_factory() as session:
            return session.get(AgentSession, session_id)

    def update_session(
        self,
        *,
        session_id: int,
        status: str,
        customer_id: str | None,
        metadata_updates: dict | None = None,
        mark_ended: bool = True,
    ) -> None:
        with self._session_factory() as session:
            record = session.get(AgentSession, session_id)
            if record is None:
                return

            record.status = status
            record.ended_at = datetime.now(timezone.utc) if mark_ended else None
            if customer_id:
                record.customer_id = customer_id
            if metadata_updates:
                merged = dict(record.metadata_json or {})
                merged.update(metadata_updates)
                record.metadata_json = merged
            session.commit()
