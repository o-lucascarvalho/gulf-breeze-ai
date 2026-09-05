from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import logging

from sqlalchemy import Select, cast, desc, func, or_, select, String

from app.agents.context import ConversationContext
from app.db.models import AgentAction, AgentSession, Customer, Employee, Invoice, Job, JobEmployee, JobScheduleOverride
from app.db.session import SessionLocal
from app.repositories.customer_repository import CustomerRepository
from app.services.schedule_overrides import ScheduleOverrideService
from app.services.weather import WeatherService


LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class DashboardServiceError(Exception):
    code: str
    message: str
    status_code: int


class DashboardService:
    def __init__(
        self,
        session_factory=SessionLocal,
        *,
        schedule_service: ScheduleOverrideService | None = None,
        weather_service: WeatherService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._schedule_service = schedule_service or ScheduleOverrideService()
        self._weather_service = weather_service or WeatherService()
        self._customer_repository = CustomerRepository()

    def get_summary(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            customers = int(session.scalar(select(func.count()).select_from(Customer)) or 0)
            jobs = int(session.scalar(select(func.count()).select_from(Job)) or 0)
            ai_conversations = int(session.scalar(select(func.count()).select_from(AgentSession)) or 0)
            active_sessions = int(
                session.scalar(select(func.count()).select_from(AgentSession).where(AgentSession.status == "active")) or 0
            )
            human_handoffs = int(
                session.scalar(select(func.count()).select_from(AgentSession).where(AgentSession.status == "handoff")) or 0
            )
            upcoming_appointments = self._schedule_service.count_upcoming_jobs(session, now_utc=now)
            open_invoices = int(
                session.scalar(select(func.count()).select_from(Invoice).where(Invoice.due_amount.is_not(None), Invoice.due_amount > 0))
                or 0
            )
            outstanding_balance = session.scalar(
                select(func.coalesce(func.sum(Invoice.due_amount), 0)).where(Invoice.due_amount.is_not(None), Invoice.due_amount > 0)
            ) or Decimal("0")

            recent_sessions = self._list_session_rows(session, limit=5)
            appointments = self._list_appointment_rows(session, limit=5, now=now)
            recent_schedule_changes = self._list_schedule_changes_rows(session, limit=5, offset=0)["items"]
            outstanding_accounts = self._list_outstanding_accounts(session, limit=5)

        return {
            "customers": customers,
            "jobs": jobs,
            "ai_conversations": ai_conversations,
            "active_sessions": active_sessions,
            "human_handoffs": human_handoffs,
            "upcoming_appointments": upcoming_appointments,
            "open_invoices": open_invoices,
            "outstanding_balance": float(outstanding_balance),
            "recent_sessions": recent_sessions,
            "appointments": appointments,
            "recent_schedule_changes": recent_schedule_changes,
            "outstanding_accounts": outstanding_accounts,
        }

    def list_appointments(
        self,
        *,
        q: str | None,
        from_at: datetime | None,
        to_at: datetime | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        latest = self._schedule_service.latest_active_overrides_subquery()
        effective_start = func.coalesce(latest.c.new_start_at, Job.scheduled_start)

        with self._session_factory() as session:
            query = (
                select(
                    Job,
                    Customer,
                    latest.c.id,
                    latest.c.original_start_at,
                    latest.c.original_end_at,
                    effective_start.label("effective_start_at"),
                    func.coalesce(latest.c.new_end_at, Job.scheduled_end).label("effective_end_at"),
                )
                .join(Customer, Customer.id == Job.customer_id, isouter=True)
                .join(latest, latest.c.job_id == Job.id, isouter=True)
                .where(effective_start.is_not(None), effective_start >= now)
            )

            if from_at is not None:
                query = query.where(effective_start >= from_at)
            if to_at is not None:
                query = query.where(effective_start <= to_at)

            normalized_q = (q or "").strip().lower()
            if normalized_q:
                needle = f"%{normalized_q}%"
                query = (
                    query
                    .join(JobEmployee, JobEmployee.job_id == Job.id, isouter=True)
                    .join(Employee, Employee.id == JobEmployee.employee_id, isouter=True)
                )
                query = query.where(
                    or_(
                        func.lower(func.coalesce(Customer.first_name, "")).like(needle),
                        func.lower(func.coalesce(Customer.last_name, "")).like(needle),
                        func.lower(func.coalesce(Customer.company, "")).like(needle),
                        func.lower(func.coalesce(Job.description, "")).like(needle),
                        func.lower(func.coalesce(Job.street, "")).like(needle),
                        func.lower(func.coalesce(Job.city, "")).like(needle),
                        func.lower(func.coalesce(Job.state, "")).like(needle),
                        func.lower(func.coalesce(Job.zip, "")).like(needle),
                        func.lower(func.coalesce(Employee.first_name, "")).like(needle),
                        func.lower(func.coalesce(Employee.last_name, "")).like(needle),
                        func.lower(cast(Job.id, String)).like(needle),
                    )
                )

            count_stmt = select(func.count()).select_from(query.distinct().subquery())
            total = int(session.scalar(count_stmt) or 0)

            rows = (
                session.execute(
                    query.distinct()
                    .order_by(effective_start.asc(), Job.id.asc())
                    .offset(offset)
                    .limit(limit)
                )
                .all()
            )

            job_ids = [job.id for job, *_ in rows]
            employee_map = self._customer_repository.get_job_employees(session, job_ids)

            items = []
            for (
                job,
                customer,
                override_id,
                override_original_start,
                override_original_end,
                effective_start_at,
                effective_end_at,
            ) in rows:
                weather = None
                try:
                    weather = self._weather_service.get_weather_for_appointment(job, effective_start_at)
                except Exception as exc:  # pragma: no cover
                    LOGGER.warning("appointments_weather_enrichment_failed job_id=%s error=%s", job.id, exc)

                technicians = [
                    " ".join(part for part in [employee.first_name, employee.last_name] if part).strip() or employee.id
                    for employee in employee_map.get(job.id, [])
                ]

                items.append(
                    {
                        "job_id": job.id,
                        "customer_name": self._customer_name(customer),
                        "contact_summary": self._customer_contact_summary(customer),
                        "description": job.description,
                        "address": self._compact_job_address(job),
                        "technicians": technicians,
                        "scheduled_start": job.scheduled_start,
                        "scheduled_end": job.scheduled_end,
                        "effective_start_at": effective_start_at,
                        "effective_end_at": effective_end_at,
                        "original_start_at": override_original_start or job.scheduled_start,
                        "original_end_at": override_original_end or job.scheduled_end,
                        "status": job.work_status,
                        "has_schedule_override": bool(override_id),
                        "weather": weather,
                    }
                )

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def list_schedule_changes(
        self,
        *,
        q: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._list_schedule_changes_rows(
                session,
                q=q,
                from_at=from_at,
                to_at=to_at,
                limit=limit,
                offset=offset,
            )

    def _list_schedule_changes_rows(
        self,
        session,
        *,
        q: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        query = (
            select(JobScheduleOverride, Job, Customer, AgentSession)
            .join(Job, Job.id == JobScheduleOverride.job_id)
            .join(Customer, Customer.id == Job.customer_id, isouter=True)
            .join(AgentSession, AgentSession.id == JobScheduleOverride.created_by_agent_session_id, isouter=True)
        )

        if from_at is not None:
            query = query.where(JobScheduleOverride.created_at >= from_at)
        if to_at is not None:
            query = query.where(JobScheduleOverride.created_at <= to_at)

        normalized_q = (q or "").strip().lower()
        if normalized_q:
            needle = f"%{normalized_q}%"
            query = query.where(
                or_(
                    func.lower(func.coalesce(Customer.first_name, "")).like(needle),
                    func.lower(func.coalesce(Customer.last_name, "")).like(needle),
                    func.lower(func.coalesce(Customer.company, "")).like(needle),
                    func.lower(func.coalesce(Job.description, "")).like(needle),
                    func.lower(func.coalesce(Job.street, "")).like(needle),
                    func.lower(func.coalesce(Job.city, "")).like(needle),
                    func.lower(func.coalesce(Job.state, "")).like(needle),
                    func.lower(func.coalesce(Job.zip, "")).like(needle),
                    func.lower(func.coalesce(JobScheduleOverride.reason, "")).like(needle),
                    func.lower(cast(Job.id, String)).like(needle),
                    func.lower(cast(JobScheduleOverride.created_by_agent_session_id, String)).like(needle),
                )
            )

        total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        rows = (
            session.execute(
                query.order_by(JobScheduleOverride.created_at.desc(), JobScheduleOverride.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .all()
        )

        items = []
        for override, job, customer, agent_session in rows:
            items.append(
                {
                    "change_id": int(override.id),
                    "job_id": override.job_id,
                    "customer_name": self._customer_name(customer),
                    "address": self._compact_job_address(job),
                    "description": job.description,
                    "original_start_at": override.original_start_at,
                    "original_end_at": override.original_end_at,
                    "new_start_at": override.new_start_at,
                    "new_end_at": override.new_end_at,
                    "reason": override.reason,
                    "status": override.status,
                    "created_at": override.created_at,
                    "agent_session_id": int(override.created_by_agent_session_id) if override.created_by_agent_session_id else None,
                    "channel": agent_session.channel if agent_session else None,
                    "changed_by": self._changed_by_label(agent_session.channel if agent_session else None),
                }
            )

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def list_sessions(self, *, limit: int) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._list_session_rows(session, limit=limit)

    def get_session_detail(self, session_id: int) -> dict[str, Any]:
        with self._session_factory() as session:
            record = session.get(AgentSession, session_id)
            if record is None:
                raise DashboardServiceError(
                    code="session_not_found",
                    message="The requested session was not found.",
                    status_code=404,
                )

            session_summary = self._serialize_session(record, action_count=self._action_count(session, session_id))
            actions = session.scalars(
                select(AgentAction)
                .where(AgentAction.session_id == session_id)
                .order_by(AgentAction.timestamp.asc(), AgentAction.id.asc())
            ).all()
            context = ConversationContext.from_metadata(record.metadata_json)

        return {
            "session": session_summary,
            "conversation": [
                {
                    "role": item.role,
                    "content": item.content,
                    "tool_call_id": item.tool_call_id,
                }
                for item in context.messages
            ],
            "actions": [self._serialize_action(item) for item in actions],
        }

    def list_handoffs(self, *, limit: int) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(AgentSession)
                .where(AgentSession.status == "handoff")
                .order_by(desc(AgentSession.started_at), desc(AgentSession.id))
                .limit(limit)
            ).all()
            action_counts = self._action_counts(session, [int(row.id) for row in rows])

        return [
            {
                **self._serialize_session(row, action_count=action_counts.get(int(row.id), 0)),
                "handoff_reason": (row.metadata_json or {}).get("handoff_reason"),
            }
            for row in rows
        ]

    def _list_session_rows(self, session, *, limit: int) -> list[dict[str, Any]]:
        rows = session.scalars(
            select(AgentSession).order_by(desc(AgentSession.started_at), desc(AgentSession.id)).limit(limit)
        ).all()
        action_counts = self._action_counts(session, [int(row.id) for row in rows])
        return [self._serialize_session(row, action_count=action_counts.get(int(row.id), 0)) for row in rows]

    def _list_appointment_rows(self, session, *, limit: int, now: datetime) -> list[dict[str, Any]]:
        jobs_with_schedule = self._schedule_service.list_upcoming_jobs(session, now_utc=now, limit=limit)
        job_ids = [job.id for job, _ in jobs_with_schedule]
        customer_ids = [job.customer_id for job, _ in jobs_with_schedule if job.customer_id]

        customers = {
            row.id: row
            for row in session.scalars(select(Customer).where(Customer.id.in_(customer_ids))).all()
        } if customer_ids else {}
        employee_map = self._customer_repository.get_job_employees(session, job_ids)

        items: list[dict[str, Any]] = []
        for job, effective in jobs_with_schedule:
            customer = customers.get(job.customer_id) if job.customer_id else None
            technicians = [
                " ".join(part for part in [employee.first_name, employee.last_name] if part).strip() or employee.id
                for employee in employee_map.get(job.id, [])
            ]

            weather = None
            try:
                weather = self._weather_service.get_weather_for_appointment(job, effective.effective_start_at)
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("dashboard_weather_enrichment_failed job_id=%s error=%s", job.id, exc)

            items.append(
                {
                    "job_id": job.id,
                    "customer_name": self._customer_name(customer),
                    "description": job.description,
                    "address": self._compact_job_address(job),
                    "technicians": technicians,
                    "scheduled_start": job.scheduled_start,
                    "scheduled_end": job.scheduled_end,
                    "effective_start_at": effective.effective_start_at,
                    "effective_end_at": effective.effective_end_at,
                    "original_start_at": effective.original_start_at,
                    "original_end_at": effective.original_end_at,
                    "status": job.work_status,
                    "has_schedule_override": bool(effective.active_override_id),
                    "contact_summary": self._customer_contact_summary(customer),
                    "weather": weather,
                }
            )

        return items

    def _list_outstanding_accounts(self, session, *, limit: int) -> list[dict[str, Any]]:
        rows = session.execute(
            select(Invoice, Job, Customer)
            .join(Job, Job.id == Invoice.job_id)
            .join(Customer, Customer.id == Job.customer_id, isouter=True)
            .where(Invoice.due_amount.is_not(None), Invoice.due_amount > 0)
            .order_by(Invoice.due_amount.desc(), Invoice.invoice_date.desc().nullslast(), Invoice.id.asc())
            .limit(limit)
        ).all()
        return [
            {
                "invoice_id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "customer_name": self._customer_name(customer),
                "job_description": job.description,
                "status": invoice.status,
                "due_amount": float(invoice.due_amount or 0),
                "service_date": invoice.service_date,
            }
            for invoice, job, customer in rows
        ]

    def _serialize_session(self, record: AgentSession, *, action_count: int) -> dict[str, Any]:
        context = ConversationContext.from_metadata(record.metadata_json)
        metadata = record.metadata_json or {}
        first_user_message = next((item.content for item in context.messages if item.role == "user" and item.content.strip()), None)
        return {
            "session_id": int(record.id),
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "channel": record.channel,
            "status": record.status,
            "customer_id": record.customer_id,
            "customer_name": context.customer_display_name,
            "summary": first_user_message or "Conversation started",
            "message_count": len(context.messages),
            "action_count": action_count,
            "needs_handoff": bool(metadata.get("needs_handoff") or record.status == "handoff"),
            "handoff_reason": metadata.get("handoff_reason"),
        }

    def _serialize_action(self, action: AgentAction) -> dict[str, Any]:
        return {
            "id": int(action.id),
            "timestamp": action.timestamp,
            "action_type": action.action_type,
            "display_label": self._action_label(action.action_type),
            "detail": self._action_detail(action),
            "entity_type": action.entity_type,
            "entity_id": action.entity_id,
            "status": action.status,
            "latency_ms": action.latency_ms,
            "arguments": action.arguments or {},
            "result": action.result,
        }

    @staticmethod
    def _action_detail(action: AgentAction) -> str | None:
        if action.action_type != "reschedule_appointment":
            if action.action_type != "get_weather_forecast":
                return None

        payload = action.result or {}
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None

        if action.action_type == "get_weather_forecast":
            location = data.get("location")
            forecast_at = data.get("forecast_at")
            if data.get("available"):
                parts = [part for part in [location, forecast_at] if part]
                summary = " · ".join(parts) if parts else None
                condition = data.get("condition")
                temperature = data.get("temperature_f")
                rain = data.get("precipitation_probability")
                weather_bits = [
                    f"{temperature}F" if temperature is not None else None,
                    f"{rain}% rain" if rain is not None else None,
                    condition,
                ]
                weather_text = " · ".join(bit for bit in weather_bits if bit)
                if summary and weather_text:
                    return f"{summary} · {weather_text}"
                return summary or weather_text
            reason = data.get("reason")
            if location and reason:
                return f"{location} · {reason.replace('_', ' ')}"
            return reason.replace('_', ' ') if reason else None

        original = data.get("original_start_at")
        effective = data.get("effective_start_at")
        if not original or not effective:
            return None
        return f"{original} -> {effective}"

    @staticmethod
    def _action_label(action_type: str) -> str:
        labels = {
            "search_customers": "Searched customers",
            "semantic_search_notes": "Reviewed service notes",
            "get_customer_balance": "Checked outstanding balance",
            "get_note": "Opened service note",
            "get_upcoming_appointments": "Checked upcoming appointments",
            "get_job": "Reviewed job details",
            "get_customer_jobs": "Reviewed service history",
            "get_job_invoice": "Checked invoice details",
            "get_customer": "Retrieved customer profile",
            "reschedule_appointment": "Rescheduled appointment",
            "get_weather_forecast": "Checked weather forecast",
            "request_handoff": "Requested human handoff",
        }
        return labels.get(action_type, action_type.replace("_", " ").title())

    @staticmethod
    def _customer_name(customer: Customer | None) -> str | None:
        if customer is None:
            return None
        full_name = " ".join(part for part in [customer.first_name, customer.last_name] if part).strip()
        return full_name or customer.company

    @staticmethod
    def _compact_job_address(job: Job) -> str | None:
        parts = [part for part in [job.street, job.street_line_2, job.city, job.state, job.zip] if part]
        if not parts:
            return None
        return ", ".join(parts)

    @staticmethod
    def _customer_contact_summary(customer: Customer | None) -> str | None:
        if customer is None:
            return None
        # Canonical imported schema currently does not persist direct phone/email fields.
        return None

    @staticmethod
    def _changed_by_label(channel: str | None) -> str:
        if not channel:
            return "System / Unknown"
        normalized = channel.strip().lower()
        if normalized == "web":
            via = "Web Chat"
        elif normalized == "web_voice":
            via = "Browser Voice"
        elif normalized in {"voice", "phone", "pstn"}:
            via = "Phone"
        else:
            via = normalized.replace("_", " ").title()
        return f"Gulf Breeze AI · {via}"

    @staticmethod
    def _action_count(session, session_id: int) -> int:
        return int(
            session.scalar(select(func.count()).select_from(AgentAction).where(AgentAction.session_id == session_id)) or 0
        )

    @staticmethod
    def _action_counts(session, session_ids: list[int]) -> dict[int, int]:
        if not session_ids:
            return {}
        rows = session.execute(
            select(AgentAction.session_id, func.count())
            .where(AgentAction.session_id.in_(session_ids))
            .group_by(AgentAction.session_id)
        ).all()
        return {int(session_id): int(count) for session_id, count in rows}