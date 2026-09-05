from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select

from app.db.models import VoiceCall
from app.db.session import SessionLocal


class VoiceCallService:
    def __init__(self, session_factory=SessionLocal) -> None:
        self._session_factory = session_factory

    def create_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
        agent_session_id: int,
        caller_identifier: str | None,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            record = VoiceCall(
                provider=provider,
                provider_call_id=provider_call_id,
                agent_session_id=agent_session_id,
                caller_identifier=caller_identifier,
                status=status,
                started_at=datetime.now(timezone.utc),
                metadata_json=metadata or {},
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return self._serialize(record)

    def get_call(self, voice_call_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            record = session.get(VoiceCall, voice_call_id)
            return self._serialize(record) if record else None

    def get_by_provider_call_id(self, *, provider: str, provider_call_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            statement = select(VoiceCall).where(
                VoiceCall.provider == provider,
                VoiceCall.provider_call_id == provider_call_id,
            )
            record = session.scalar(statement)
            return self._serialize(record) if record else None

    def update_call(
        self,
        *,
        voice_call_id: int,
        status: str | None = None,
        ended_at: datetime | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            record = session.get(VoiceCall, voice_call_id)
            if record is None:
                return None

            if status is not None:
                record.status = status
            if ended_at is not None:
                record.ended_at = ended_at
            if metadata_updates:
                merged = dict(record.metadata_json or {})
                merged.update(metadata_updates)
                record.metadata_json = merged

            session.commit()
            session.refresh(record)
            return self._serialize(record)

    def list_calls(self, *, limit: int, status: str | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(VoiceCall)
            if status:
                statement = statement.where(VoiceCall.status == status)
            statement = statement.order_by(desc(VoiceCall.started_at), desc(VoiceCall.id)).limit(limit)
            rows = session.scalars(statement).all()
            return [self._serialize(row) for row in rows]

    @staticmethod
    def _serialize(record: VoiceCall | None) -> dict[str, Any]:
        if record is None:
            return {}
        return {
            "id": record.id,
            "provider": record.provider,
            "provider_call_id": record.provider_call_id,
            "agent_session_id": record.agent_session_id,
            "caller_identifier": record.caller_identifier,
            "status": record.status,
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "metadata": record.metadata_json or {},
        }
