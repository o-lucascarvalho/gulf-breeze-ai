from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.repositories.customer_repository import CustomerRepository
from app.services.schedule_overrides import ScheduleOverrideService
from app.tools.helpers import compact_address, to_jsonable
from app.tools.types import ToolDefinition


class SearchCustomersInput(BaseModel):
    name: str | None = None
    company: str | None = None
    limit: int = Field(default=5, ge=1, le=25)

    @model_validator(mode="after")
    def require_name_or_company(self) -> "SearchCustomersInput":
        has_name = bool(self.name and self.name.strip())
        has_company = bool(self.company and self.company.strip())
        if not has_name and not has_company:
            raise ValueError("Either name or company must be provided")
        return self


class GetCustomerInput(BaseModel):
    customer_id: str


class GetCustomerJobsInput(BaseModel):
    customer_id: str
    limit: int = Field(default=10, ge=1, le=25)
    status: str | None = None


class GetCustomerBalanceInput(BaseModel):
    customer_id: str


class GetUpcomingAppointmentsInput(BaseModel):
    customer_id: str
    limit: int = Field(default=5, ge=1, le=25)


def build_customer_tools(session_factory) -> list[ToolDefinition]:
    repository = CustomerRepository()
    schedule_service = ScheduleOverrideService()

    def search_customers(args: SearchCustomersInput) -> dict:
        with session_factory() as session:
            matches = repository.search_customers(
                session,
                name=args.name,
                company=args.company,
                limit=args.limit,
            )

        items = [
            {
                "customer_id": row.id,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "company": row.company,
                "kind": row.kind,
            }
            for row in matches
        ]
        return {
            "matches": to_jsonable(items),
            "ambiguous": len(items) > 1,
            "count": len(items),
        }

    def get_customer(args: GetCustomerInput) -> dict:
        with session_factory() as session:
            customer = repository.get_customer(session, args.customer_id)
            if customer is None:
                return {"found": False, "customer": None}

            addresses = repository.get_customer_addresses(session, args.customer_id)

        return {
            "found": True,
            "customer": to_jsonable(
                {
                    "customer_id": customer.id,
                    "first_name": customer.first_name,
                    "last_name": customer.last_name,
                    "company": customer.company,
                    "kind": customer.kind,
                    "job_count": customer.job_count,
                    "first_job": customer.first_job,
                    "last_job": customer.last_job,
                    "addresses": [
                        {
                            "address_id": address.id,
                            "street": address.street,
                            "street_line_2": address.street_line_2,
                            "city": address.city,
                            "state": address.state,
                            "zip": address.zip,
                            "normalized_address": address.normalized_address,
                        }
                        for address in addresses
                    ],
                }
            ),
        }

    def get_customer_jobs(args: GetCustomerJobsInput) -> dict:
        with session_factory() as session:
            customer = repository.get_customer(session, args.customer_id)
            if customer is None:
                return {"found": False, "jobs": []}

            jobs = repository.get_customer_jobs(
                session,
                customer_id=args.customer_id,
                limit=args.limit,
                status=args.status,
            )
            employee_map = repository.get_job_employees(session, [job.id for job in jobs])

        items = []
        for job in jobs:
            employees = [
                {
                    "employee_id": employee.id,
                    "first_name": employee.first_name,
                    "last_name": employee.last_name,
                    "role": employee.role,
                }
                for employee in employee_map.get(job.id, [])
            ]
            items.append(
                {
                    "job_id": job.id,
                    "description": job.description,
                    "work_status": job.work_status,
                    "scheduled_start": job.scheduled_start,
                    "scheduled_end": job.scheduled_end,
                    "invoice_number": job.invoice_number,
                    "total_amount": job.total_amount,
                    "outstanding_balance": job.outstanding_balance,
                    "service_address": compact_address(job.street, job.street_line_2, job.city, job.state, job.zip),
                    "employees": employees,
                }
            )

        return {
            "found": True,
            "customer_id": args.customer_id,
            "jobs": to_jsonable(items),
            "count": len(items),
        }

    def get_customer_balance(args: GetCustomerBalanceInput) -> dict:
        with session_factory() as session:
            customer = repository.get_customer(session, args.customer_id)
            if customer is None:
                return {
                    "found": False,
                    "customer_id": args.customer_id,
                    "total_outstanding_balance": "0.00",
                    "open_jobs_or_invoices": 0,
                }

            total_outstanding, open_jobs = repository.get_customer_balance_summary(session, args.customer_id)

        return {
            "found": True,
            "customer_id": args.customer_id,
            "total_outstanding_balance": to_jsonable(total_outstanding),
            "open_jobs_or_invoices": open_jobs,
        }

    def get_upcoming_appointments(args: GetUpcomingAppointmentsInput) -> dict:
        now_utc = datetime.now(timezone.utc)
        with session_factory() as session:
            customer = repository.get_customer(session, args.customer_id)
            if customer is None:
                return {
                    "found": False,
                    "customer_id": args.customer_id,
                    "appointments": [],
                }

            jobs_with_schedule = schedule_service.list_upcoming_for_customer(
                session,
                customer_id=args.customer_id,
                now_utc=now_utc,
                limit=args.limit,
            )

        items = []
        for job, effective in jobs_with_schedule:
            items.append(
                {
                    "job_id": job.id,
                    "scheduled_start": job.scheduled_start,
                    "scheduled_end": job.scheduled_end,
                    "effective_start_at": effective.effective_start_at,
                    "effective_end_at": effective.effective_end_at,
                    "arrival_window_minutes": job.arrival_window_minutes,
                    "work_status": job.work_status,
                    "description": job.description,
                    "address": compact_address(job.street, job.street_line_2, job.city, job.state, job.zip),
                    "has_schedule_override": bool(effective.active_override_id),
                }
            )

        return {
            "found": True,
            "customer_id": args.customer_id,
            "appointments": to_jsonable(items),
            "count": len(items),
        }

    return [
        ToolDefinition(
            name="search_customers",
            description=(
                "Resolve customers by person name or company. Use this first when the user mentions a human-readable "
                "customer identity and a customer_id is not yet known. Returns all matches and ambiguity info."
            ),
            input_model=SearchCustomersInput,
            handler=search_customers,
        ),
        ToolDefinition(
            name="get_customer",
            description=(
                "Get compact customer details and known addresses by customer_id. Use this for authoritative profile "
                "facts, not for note semantics."
            ),
            input_model=GetCustomerInput,
            handler=get_customer,
        ),
        ToolDefinition(
            name="get_customer_jobs",
            description=(
                "Get recent jobs for a customer_id with deterministic ordering and optional status filter. Use for "
                "service history, recent visits, and schedule-oriented summaries."
            ),
            input_model=GetCustomerJobsInput,
            handler=get_customer_jobs,
        ),
        ToolDefinition(
            name="get_customer_balance",
            description=(
                "Return authoritative outstanding balance for a customer_id. Use this for money owed questions, not "
                "semantic notes."
            ),
            input_model=GetCustomerBalanceInput,
            handler=get_customer_balance,
        ),
        ToolDefinition(
            name="get_upcoming_appointments",
            description=(
                "Return future scheduled jobs for a customer_id. Use this for upcoming appointment questions."
            ),
            input_model=GetUpcomingAppointmentsInput,
            handler=get_upcoming_appointments,
        ),
    ]
