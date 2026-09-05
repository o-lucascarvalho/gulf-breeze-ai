from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.schedule_overrides import MIAMI_TZ, ScheduleOverrideService
from app.tools.helpers import to_jsonable
from app.tools.types import ToolDefinition


class RescheduleAppointmentInput(BaseModel):
    job_id: str
    new_start_at: datetime
    new_end_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=24 * 60)
    reason: str = Field(min_length=5, max_length=500)
    agent_session_id: int | None = None
    idempotency_key: str | None = None


def build_schedule_tools(session_factory) -> list[ToolDefinition]:
    schedule_service = ScheduleOverrideService()

    def reschedule_appointment(args: RescheduleAppointmentInput) -> dict:
        with session_factory() as session:
            result = schedule_service.reschedule_job(
                session,
                job_id=args.job_id,
                new_start_at=args.new_start_at,
                new_end_at=args.new_end_at,
                duration_minutes=args.duration_minutes,
                reason=args.reason,
                agent_session_id=args.agent_session_id,
                idempotency_key=args.idempotency_key,
            )

        return to_jsonable(
            {
                "job_id": result["job_id"],
                "override_id": result["override_id"],
                "idempotent": result["idempotent"],
                "status": result["status"],
                "reason": result["reason"],
                "original_start_at": result["original_start_at"],
                "original_end_at": result["original_end_at"],
                "effective_start_at": result["effective_start_at"],
                "effective_end_at": result["effective_end_at"],
                "timezone": str(MIAMI_TZ),
            }
        )

    return [
        ToolDefinition(
            name="reschedule_appointment",
            description=(
                "Reschedule one job appointment by creating an auditable schedule override. "
                "Use only after customer and target appointment are unambiguous. "
                "Treat appointment times as Miami local wall-clock values; the backend converts them to UTC. "
                "If only new_start_at is provided, original duration is preserved when available."
            ),
            input_model=RescheduleAppointmentInput,
            handler=reschedule_appointment,
        )
    ]
