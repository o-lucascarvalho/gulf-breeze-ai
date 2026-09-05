from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Customer, CustomerAddress, Employee, Job, JobEmployee


class CustomerRepository:
    def search_customers(
        self,
        session: Session,
        *,
        name: str | None,
        company: str | None,
        limit: int,
    ) -> list[Customer]:
        stmt = select(Customer)
        predicates = []

        if name:
            normalized_name = name.strip().lower()
            if normalized_name:
                full_name = func.lower(
                    func.concat(
                        func.coalesce(Customer.first_name, ""),
                        " ",
                        func.coalesce(Customer.last_name, ""),
                    )
                )
                predicates.append(full_name.like(f"%{normalized_name}%"))

        if company:
            normalized_company = company.strip().lower()
            if normalized_company:
                predicates.append(func.lower(func.coalesce(Customer.company, "")).like(f"%{normalized_company}%"))

        if predicates:
            stmt = stmt.where(and_(*predicates))

        stmt = stmt.order_by(
            func.lower(func.coalesce(Customer.last_name, "")).asc(),
            func.lower(func.coalesce(Customer.first_name, "")).asc(),
            Customer.id.asc(),
        ).limit(limit)
        return list(session.scalars(stmt).all())

    def get_customer(self, session: Session, customer_id: str) -> Customer | None:
        return session.get(Customer, customer_id)

    def get_customer_addresses(self, session: Session, customer_id: str) -> list[CustomerAddress]:
        stmt = (
            select(CustomerAddress)
            .where(CustomerAddress.customer_id == customer_id)
            .order_by(CustomerAddress.id.asc())
        )
        return list(session.scalars(stmt).all())

    def get_customer_jobs(
        self,
        session: Session,
        *,
        customer_id: str,
        limit: int,
        status: str | None,
    ) -> list[Job]:
        stmt = select(Job).where(Job.customer_id == customer_id)
        if status:
            stmt = stmt.where(func.lower(func.coalesce(Job.work_status, "")) == status.strip().lower())

        stmt = stmt.order_by(
            Job.scheduled_start.desc().nulls_last(),
            Job.created_at.desc().nulls_last(),
            Job.id.asc(),
        ).limit(limit)
        return list(session.scalars(stmt).all())

    def get_job_employees(self, session: Session, job_ids: list[str]) -> dict[str, list[Employee]]:
        if not job_ids:
            return {}

        stmt = (
            select(JobEmployee.job_id, Employee)
            .join(Employee, Employee.id == JobEmployee.employee_id)
            .where(JobEmployee.job_id.in_(job_ids))
            .order_by(JobEmployee.job_id.asc(), Employee.last_name.asc().nulls_last(), Employee.first_name.asc().nulls_last(), Employee.id.asc())
        )

        mapping: dict[str, list[Employee]] = {job_id: [] for job_id in job_ids}
        for job_id, employee in session.execute(stmt).all():
            mapping.setdefault(job_id, []).append(employee)
        return mapping

    def get_customer_balance_summary(self, session: Session, customer_id: str) -> tuple[Decimal, int]:
        stmt = select(
            func.coalesce(func.sum(Job.outstanding_balance), Decimal("0.00")),
            func.count().filter(Job.outstanding_balance > 0),
        ).where(Job.customer_id == customer_id)
        total_outstanding, open_jobs = session.execute(stmt).one()
        return Decimal(total_outstanding or Decimal("0.00")), int(open_jobs or 0)

    def get_upcoming_appointments(
        self,
        session: Session,
        *,
        customer_id: str,
        now_utc: datetime,
        limit: int,
    ) -> list[Job]:
        stmt = (
            select(Job)
            .where(
                Job.customer_id == customer_id,
                Job.scheduled_start.is_not(None),
                Job.scheduled_start >= now_utc,
            )
            .order_by(Job.scheduled_start.asc(), Job.id.asc())
            .limit(limit)
        )
        return list(session.scalars(stmt).all())
