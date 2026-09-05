from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Employee, Job, JobEmployee, JobScheduleOverride
from app.tools.types import ToolHandledError

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_CANCELLED = "cancelled"
MIAMI_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class EffectiveSchedule:
    job_id: str
    original_start_at: datetime | None
    original_end_at: datetime | None
    effective_start_at: datetime | None
    effective_end_at: datetime | None
    active_override_id: int | None


class ScheduleOverrideService:
    def latest_active_overrides_subquery(self):
        return self._latest_active_subquery()

    @staticmethod
    def coerce_miami_wall_time(value: datetime) -> datetime:
        return value.replace(tzinfo=None).replace(tzinfo=MIAMI_TZ)

    def _latest_active_subquery(self):
        ranked = (
            select(
                JobScheduleOverride.id.label("id"),
                JobScheduleOverride.job_id.label("job_id"),
                JobScheduleOverride.original_start_at.label("original_start_at"),
                JobScheduleOverride.original_end_at.label("original_end_at"),
                JobScheduleOverride.new_start_at.label("new_start_at"),
                JobScheduleOverride.new_end_at.label("new_end_at"),
                JobScheduleOverride.created_by_agent_session_id.label("created_by_agent_session_id"),
                JobScheduleOverride.reason.label("reason"),
                func.row_number()
                .over(
                    partition_by=JobScheduleOverride.job_id,
                    order_by=(JobScheduleOverride.created_at.desc(), JobScheduleOverride.id.desc()),
                )
                .label("rn"),
            )
            .where(JobScheduleOverride.status == STATUS_ACTIVE)
            .subquery("job_schedule_overrides_ranked")
        )
        return select(ranked).where(ranked.c.rn == 1).subquery("job_schedule_overrides_latest")

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return ScheduleOverrideService.coerce_miami_wall_time(value).astimezone(timezone.utc)

    def resolve_for_jobs(self, session: Session, jobs: list[Job]) -> dict[str, EffectiveSchedule]:
        if not jobs:
            return {}

        job_ids = [job.id for job in jobs]
        latest = self._latest_active_subquery()
        rows = session.execute(
            select(
                Job.id,
                Job.scheduled_start,
                Job.scheduled_end,
                latest.c.id,
                latest.c.original_start_at,
                latest.c.original_end_at,
                latest.c.new_start_at,
                latest.c.new_end_at,
            )
            .select_from(Job)
            .join(latest, latest.c.job_id == Job.id, isouter=True)
            .where(Job.id.in_(job_ids))
        ).all()

        output: dict[str, EffectiveSchedule] = {}
        for (
            job_id,
            scheduled_start,
            scheduled_end,
            override_id,
            override_original_start,
            override_original_end,
            new_start_at,
            new_end_at,
        ) in rows:
            output[job_id] = EffectiveSchedule(
                job_id=job_id,
                original_start_at=override_original_start or scheduled_start,
                original_end_at=override_original_end or scheduled_end,
                effective_start_at=new_start_at or scheduled_start,
                effective_end_at=new_end_at or scheduled_end,
                active_override_id=override_id,
            )
        return output

    def list_upcoming_for_customer(
        self,
        session: Session,
        *,
        customer_id: str,
        now_utc: datetime,
        limit: int,
    ) -> list[tuple[Job, EffectiveSchedule]]:
        latest = self._latest_active_subquery()
        effective_start = func.coalesce(latest.c.new_start_at, Job.scheduled_start)
        effective_end = func.coalesce(latest.c.new_end_at, Job.scheduled_end)

        rows = session.execute(
            select(
                Job,
                latest.c.id,
                latest.c.original_start_at,
                latest.c.original_end_at,
                latest.c.new_start_at,
                latest.c.new_end_at,
                effective_start.label("effective_start"),
                effective_end.label("effective_end"),
            )
            .join(latest, latest.c.job_id == Job.id, isouter=True)
            .where(Job.customer_id == customer_id)
            .where(effective_start.is_not(None), effective_start >= now_utc)
            .order_by(effective_start.asc(), Job.id.asc())
            .limit(limit)
        ).all()

        output: list[tuple[Job, EffectiveSchedule]] = []
        for (
            job,
            override_id,
            override_original_start,
            override_original_end,
            override_start,
            override_end,
            effective_start_at,
            effective_end_at,
        ) in rows:
            output.append(
                (
                    job,
                    EffectiveSchedule(
                        job_id=job.id,
                        original_start_at=override_original_start or job.scheduled_start,
                        original_end_at=override_original_end or job.scheduled_end,
                        effective_start_at=effective_start_at,
                        effective_end_at=effective_end_at,
                        active_override_id=override_id,
                    ),
                )
            )
        return output

    def list_upcoming_jobs(self, session: Session, *, now_utc: datetime, limit: int) -> list[tuple[Job, EffectiveSchedule]]:
        latest = self._latest_active_subquery()
        effective_start = func.coalesce(latest.c.new_start_at, Job.scheduled_start)
        effective_end = func.coalesce(latest.c.new_end_at, Job.scheduled_end)

        rows = session.execute(
            select(
                Job,
                latest.c.id,
                latest.c.original_start_at,
                latest.c.original_end_at,
                latest.c.new_start_at,
                latest.c.new_end_at,
                effective_start.label("effective_start"),
                effective_end.label("effective_end"),
            )
            .join(latest, latest.c.job_id == Job.id, isouter=True)
            .where(effective_start.is_not(None), effective_start >= now_utc)
            .order_by(effective_start.asc(), Job.id.asc())
            .limit(limit)
        ).all()

        output: list[tuple[Job, EffectiveSchedule]] = []
        for (
            job,
            override_id,
            override_original_start,
            override_original_end,
            override_start,
            override_end,
            effective_start_at,
            effective_end_at,
        ) in rows:
            output.append(
                (
                    job,
                    EffectiveSchedule(
                        job_id=job.id,
                        original_start_at=override_original_start or job.scheduled_start,
                        original_end_at=override_original_end or job.scheduled_end,
                        effective_start_at=effective_start_at,
                        effective_end_at=effective_end_at,
                        active_override_id=override_id,
                    ),
                )
            )
        return output

    def count_upcoming_jobs(self, session: Session, *, now_utc: datetime) -> int:
        latest = self._latest_active_subquery()
        effective_start = func.coalesce(latest.c.new_start_at, Job.scheduled_start)
        return int(
            session.scalar(
                select(func.count())
                .select_from(Job)
                .join(latest, latest.c.job_id == Job.id, isouter=True)
                .where(effective_start.is_not(None), effective_start >= now_utc)
            )
            or 0
        )

    def reschedule_job(
        self,
        session: Session,
        *,
        job_id: str,
        new_start_at: datetime,
        new_end_at: datetime | None,
        duration_minutes: int | None,
        reason: str,
        agent_session_id: int | None,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        job = session.get(Job, job_id)
        if job is None:
            raise ToolHandledError(code="job_not_found", message="The specified job was not found.")

        if not job.scheduled_start:
            raise ToolHandledError(
                code="job_not_schedulable",
                message="This job does not have a schedulable appointment window.",
            )

        normalized_status = (job.work_status or "").strip().lower()
        if "complete" in normalized_status or "canceled" in normalized_status:
            raise ToolHandledError(
                code="job_not_schedulable",
                message="This appointment is not eligible for rescheduling based on current status.",
            )

        requested_start = self._as_utc(new_start_at)
        if requested_start < datetime.now(timezone.utc) - timedelta(minutes=2):
            raise ToolHandledError(
                code="invalid_schedule_time",
                message="Requested appointment time is in the past.",
            )

        current_effective = self.resolve_for_jobs(session, [job]).get(job.id)
        current_start = current_effective.effective_start_at if current_effective else job.scheduled_start
        current_end = current_effective.effective_end_at if current_effective else job.scheduled_end

        requested_end: datetime | None
        if new_end_at is not None:
            requested_end = self._as_utc(new_end_at)
        elif duration_minutes is not None:
            requested_end = requested_start + timedelta(minutes=duration_minutes)
        elif current_start is not None and current_end is not None and current_end > current_start:
            requested_end = requested_start + (current_end - current_start)
        else:
            requested_end = None

        if requested_end is None:
            raise ToolHandledError(
                code="missing_schedule_end",
                message="A valid end time or duration is required for this appointment.",
            )

        if requested_end <= requested_start:
            raise ToolHandledError(
                code="invalid_schedule_window",
                message="Appointment end time must be after the start time.",
            )

        self._assert_no_obvious_employee_conflict(
            session,
            job_id=job.id,
            start_at=requested_start,
            end_at=requested_end,
        )

        if idempotency_key:
            duplicate = session.scalar(
                select(JobScheduleOverride)
                .where(
                    JobScheduleOverride.job_id == job.id,
                    JobScheduleOverride.status == STATUS_ACTIVE,
                    JobScheduleOverride.created_by_agent_session_id == agent_session_id,
                    JobScheduleOverride.new_start_at == requested_start,
                    JobScheduleOverride.new_end_at == requested_end,
                    JobScheduleOverride.reason == reason,
                )
                .order_by(JobScheduleOverride.created_at.desc(), JobScheduleOverride.id.desc())
            )
            if duplicate is not None:
                return {
                    "job_id": job.id,
                    "override_id": int(duplicate.id),
                    "idempotent": True,
                    "original_start_at": current_start,
                    "original_end_at": current_end,
                    "effective_start_at": duplicate.new_start_at,
                    "effective_end_at": duplicate.new_end_at,
                    "reason": duplicate.reason,
                    "status": duplicate.status,
                }

        session.execute(
            JobScheduleOverride.__table__.update()
            .where(
                JobScheduleOverride.job_id == job.id,
                JobScheduleOverride.status == STATUS_ACTIVE,
            )
            .values(status=STATUS_SUPERSEDED, updated_at=func.now())
        )

        override = JobScheduleOverride(
            job_id=job.id,
            original_start_at=current_start or job.scheduled_start,
            original_end_at=current_end,
            new_start_at=requested_start,
            new_end_at=requested_end,
            reason=reason,
            status=STATUS_ACTIVE,
            created_by_agent_session_id=agent_session_id,
        )
        session.add(override)
        session.commit()
        session.refresh(override)

        return {
            "job_id": job.id,
            "override_id": int(override.id),
            "idempotent": False,
            "original_start_at": override.original_start_at,
            "original_end_at": override.original_end_at,
            "effective_start_at": override.new_start_at,
            "effective_end_at": override.new_end_at,
            "reason": override.reason,
            "status": override.status,
        }

    def _assert_no_obvious_employee_conflict(self, session: Session, *, job_id: str, start_at: datetime, end_at: datetime) -> None:
        employee_ids = [
            employee_id
            for (employee_id,) in session.execute(
                select(JobEmployee.employee_id).where(JobEmployee.job_id == job_id)
            ).all()
        ]
        if not employee_ids:
            return

        latest = self._latest_active_subquery()
        effective_start = func.coalesce(latest.c.new_start_at, Job.scheduled_start)
        effective_end = func.coalesce(latest.c.new_end_at, Job.scheduled_end)

        rows = session.execute(
            select(Job.id)
            .join(JobEmployee, JobEmployee.job_id == Job.id)
            .join(latest, latest.c.job_id == Job.id, isouter=True)
            .where(Job.id != job_id)
            .where(JobEmployee.employee_id.in_(employee_ids))
            .where(effective_start.is_not(None), effective_end.is_not(None))
            .where(and_(effective_start < end_at, effective_end > start_at))
            .limit(1)
        ).all()

        if rows:
            conflict_job_id = rows[0][0]
            names = []
            employees = session.execute(select(Employee).where(Employee.id.in_(employee_ids))).scalars().all()
            for employee in employees:
                full_name = " ".join(part for part in [employee.first_name, employee.last_name] if part).strip() or employee.id
                names.append(full_name)
            tech_names = ", ".join(sorted(names)) if names else "assigned technician"
            raise ToolHandledError(
                code="appointment_conflict",
                message=(
                    f"Requested time conflicts with another scheduled job ({conflict_job_id}) for {tech_names}. "
                    "Please choose a different time."
                ),
            )
