from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.customer import Customer
    from app.db.models.employee import Employee
    from app.db.models.invoice import Invoice
    from app.db.models.note import Note


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_customer_id", "customer_id"),
        Index("ix_jobs_invoice_number", "invoice_number"),
        Index("ix_jobs_scheduled_start", "scheduled_start"),
        Index("ix_jobs_work_status", "work_status"),
        Index("ix_jobs_street", "street"),
        Index("ix_jobs_city_state_zip", "city", "state", "zip"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    work_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arrival_window_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_my_way_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    outstanding_balance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    street_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped["Customer | None"] = relationship(back_populates="jobs")
    notes: Mapped[list["Note"]] = relationship(back_populates="job")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="job")
    employees: Mapped[list["JobEmployee"]] = relationship(back_populates="job")
    tags: Mapped[list["JobTag"]] = relationship(back_populates="job")


class JobEmployee(Base):
    __tablename__ = "job_employees"
    __table_args__ = (
        PrimaryKeyConstraint("job_id", "employee_id"),
        Index("ix_job_employees_job_id", "job_id"),
        Index("ix_job_employees_employee_id", "employee_id"),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)

    job: Mapped[Job] = relationship(back_populates="employees")
    employee: Mapped["Employee"] = relationship(back_populates="jobs")


class JobTag(Base):
    __tablename__ = "job_tags"
    __table_args__ = (
        PrimaryKeyConstraint("job_id", "tag"),
        Index("ix_job_tags_job_id", "job_id"),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    tag: Mapped[str] = mapped_column(String(120), nullable=False)

    job: Mapped[Job] = relationship(back_populates="tags")
