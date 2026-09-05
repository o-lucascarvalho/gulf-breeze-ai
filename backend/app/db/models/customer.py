from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.agent import AgentSession
    from app.db.models.job import Job


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(100), nullable=True)
    job_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_job: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_job: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    addresses: Mapped[list["CustomerAddress"]] = relationship(back_populates="customer")
    jobs: Mapped[list["Job"]] = relationship(back_populates="customer")
    agent_sessions: Mapped[list["AgentSession"]] = relationship(back_populates="customer")


class CustomerAddress(Base):
    __tablename__ = "customer_addresses"
    __table_args__ = (
        Index("ix_customer_addresses_customer_id", "customer_id"),
        Index("ix_customer_addresses_city_state_zip", "city", "state", "zip"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    street_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    zip: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    normalized_address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="addresses")
