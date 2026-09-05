from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Customer, Employee, Invoice, Job, JobEmployee, JobTag


class JobRepository:
    def get_job(self, session: Session, job_id: str) -> Job | None:
        return session.get(Job, job_id)

    def get_job_customer(self, session: Session, job_id: str) -> Customer | None:
        stmt = (
            select(Customer)
            .join(Job, Job.customer_id == Customer.id)
            .where(Job.id == job_id)
            .limit(1)
        )
        return session.scalars(stmt).first()

    def get_job_tags(self, session: Session, job_id: str) -> list[JobTag]:
        stmt = select(JobTag).where(JobTag.job_id == job_id).order_by(JobTag.tag.asc())
        return list(session.scalars(stmt).all())

    def get_job_employees(self, session: Session, job_id: str) -> list[Employee]:
        stmt = (
            select(Employee)
            .join(JobEmployee, JobEmployee.employee_id == Employee.id)
            .where(JobEmployee.job_id == job_id)
            .order_by(Employee.last_name.asc().nulls_last(), Employee.first_name.asc().nulls_last(), Employee.id.asc())
        )
        return list(session.scalars(stmt).all())

    def get_job_invoices(self, session: Session, job_id: str) -> list[Invoice]:
        stmt = (
            select(Invoice)
            .where(Invoice.job_id == job_id)
            .order_by(
                Invoice.service_date.desc().nulls_last(),
                Invoice.invoice_date.desc().nulls_last(),
                Invoice.created_at.desc().nulls_last(),
                Invoice.id.asc(),
            )
        )
        return list(session.scalars(stmt).all())
