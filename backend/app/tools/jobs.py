from __future__ import annotations

from pydantic import BaseModel

from app.repositories.job_repository import JobRepository
from app.services.schedule_overrides import ScheduleOverrideService
from app.tools.helpers import compact_address, to_jsonable
from app.tools.types import ToolDefinition


class GetJobInput(BaseModel):
    job_id: str


def build_job_tools(session_factory) -> list[ToolDefinition]:
    repository = JobRepository()
    schedule_service = ScheduleOverrideService()

    def get_job(args: GetJobInput) -> dict:
        with session_factory() as session:
            job = repository.get_job(session, args.job_id)
            if job is None:
                return {"found": False, "job": None}

            effective = schedule_service.resolve_for_jobs(session, [job]).get(job.id)
            customer = repository.get_job_customer(session, args.job_id)
            tags = repository.get_job_tags(session, args.job_id)
            employees = repository.get_job_employees(session, args.job_id)
            invoices = repository.get_job_invoices(session, args.job_id)

        return {
            "found": True,
            "job": to_jsonable(
                {
                    "job_id": job.id,
                    "customer": {
                        "customer_id": customer.id,
                        "first_name": customer.first_name,
                        "last_name": customer.last_name,
                        "company": customer.company,
                    }
                    if customer
                    else None,
                    "description": job.description,
                    "work_status": job.work_status,
                    "scheduled_start": job.scheduled_start,
                    "scheduled_end": job.scheduled_end,
                    "effective_start_at": effective.effective_start_at if effective else job.scheduled_start,
                    "effective_end_at": effective.effective_end_at if effective else job.scheduled_end,
                    "has_schedule_override": bool(effective.active_override_id) if effective else False,
                    "on_my_way_at": job.on_my_way_at,
                    "started_at": job.started_at,
                    "completed_at": job.completed_at,
                    "arrival_window_minutes": job.arrival_window_minutes,
                    "service_address": compact_address(job.street, job.street_line_2, job.city, job.state, job.zip),
                    "tags": [tag.tag for tag in tags],
                    "employees": [
                        {
                            "employee_id": employee.id,
                            "first_name": employee.first_name,
                            "last_name": employee.last_name,
                            "role": employee.role,
                        }
                        for employee in employees
                    ],
                    "financial_summary": {
                        "total_amount": job.total_amount,
                        "outstanding_balance": job.outstanding_balance,
                        "invoice_number": job.invoice_number,
                    },
                    "invoice_summary": [
                        {
                            "invoice_id": invoice.id,
                            "invoice_number": invoice.invoice_number,
                            "status": invoice.status,
                            "amount": invoice.amount,
                            "due_amount": invoice.due_amount,
                            "service_date": invoice.service_date,
                        }
                        for invoice in invoices
                    ],
                }
            ),
        }

    return [
        ToolDefinition(
            name="get_job",
            description=(
                "Get structured job details by job_id, including status, schedule, assigned employees, tags, and "
                "financial summary. Use this for authoritative job facts."
            ),
            input_model=GetJobInput,
            handler=get_job,
        )
    ]
