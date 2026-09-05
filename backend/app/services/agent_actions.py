from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.models import AgentAction


class AgentActionService:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def log_tool_call(
        self,
        *,
        session_id: int,
        action_type: str,
        entity_type: str | None,
        entity_id: str | None,
        arguments: dict[str, Any],
        result: dict[str, Any] | None,
        status: str,
        latency_ms: int,
    ) -> None:
        with self._session_factory() as session:
            action = AgentAction(
                session_id=session_id,
                timestamp=datetime.now(timezone.utc),
                action_type=action_type,
                entity_type=entity_type,
                entity_id=entity_id,
                arguments=arguments,
                result=result,
                status=status,
                latency_ms=latency_ms,
            )
            session.add(action)
            session.commit()
