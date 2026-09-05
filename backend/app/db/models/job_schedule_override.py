from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobScheduleOverride(Base):
    __tablename__ = "job_schedule_overrides"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'superseded', 'cancelled')",
            name="ck_job_schedule_overrides_status_valid",
        ),
        Index("ix_job_schedule_overrides_job_id", "job_id"),
        Index("ix_job_schedule_overrides_status", "status"),
        Index("ix_job_schedule_overrides_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)

    original_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    new_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    new_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_agent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
