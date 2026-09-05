from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.agent import AgentSession


class VoiceCall(Base):
    __tablename__ = "voice_calls"
    __table_args__ = (
        Index("ix_voice_calls_provider_call_id", "provider_call_id"),
        Index("ix_voice_calls_started_at", "started_at"),
        Index("ix_voice_calls_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_call_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    agent_session_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    caller_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    agent_session: Mapped["AgentSession | None"] = relationship()
