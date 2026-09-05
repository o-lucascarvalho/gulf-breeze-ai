"""Create initial relational schema for V1.1 data foundation.

Revision ID: 20260903_01
Revises: 
Create Date: 2026-09-03

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260903_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=100), nullable=True),
        sa.Column("job_count", sa.Integer(), nullable=True),
        sa.Column("first_job", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_job", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
    )

    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column("job_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employees")),
    )

    op.create_table(
        "customer_addresses",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("street", sa.String(length=255), nullable=True),
        sa.Column("street_line_2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("zip", sa.String(length=20), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("normalized_address", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], name=op.f("fk_customer_addresses_customer_id_customers"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_addresses")),
    )
    op.create_index("ix_customer_addresses_customer_id", "customer_addresses", ["customer_id"], unique=False)
    op.create_index("ix_customer_addresses_city_state_zip", "customer_addresses", ["city", "state", "zip"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("work_status", sa.String(length=120), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_zone", sa.String(length=64), nullable=True),
        sa.Column("arrival_window_minutes", sa.Integer(), nullable=True),
        sa.Column("on_my_way_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("outstanding_balance", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("street", sa.String(length=255), nullable=True),
        sa.Column("street_line_2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("zip", sa.String(length=20), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], name=op.f("fk_jobs_customer_id_customers"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index("ix_jobs_customer_id", "jobs", ["customer_id"], unique=False)
    op.create_index("ix_jobs_invoice_number", "jobs", ["invoice_number"], unique=False)
    op.create_index("ix_jobs_scheduled_start", "jobs", ["scheduled_start"], unique=False)
    op.create_index("ix_jobs_work_status", "jobs", ["work_status"], unique=False)
    op.create_index("ix_jobs_street", "jobs", ["street"], unique=False)
    op.create_index("ix_jobs_city_state_zip", "jobs", ["city", "state", "zip"], unique=False)

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("caller_identifier", sa.String(length=255), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], name=op.f("fk_agent_sessions_customer_id_customers"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_sessions")),
    )

    op.create_table(
        "job_employees",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], name=op.f("fk_job_employees_employee_id_employees"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_job_employees_job_id_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "employee_id", name=op.f("pk_job_employees")),
    )
    op.create_index("ix_job_employees_job_id", "job_employees", ["job_id"], unique=False)
    op.create_index("ix_job_employees_employee_id", "job_employees", ["employee_id"], unique=False)

    op.create_table(
        "job_tags",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("tag", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_job_tags_job_id_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "tag", name=op.f("pk_job_tags")),
    )
    op.create_index("ix_job_tags_job_id", "job_tags", ["job_id"], unique=False)

    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("note_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_notes_job_id_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notes")),
        sa.UniqueConstraint("job_id", "note_index", name=op.f("uq_notes_job_id_note_index")),
    )
    op.create_index("ix_notes_job_id", "notes", ["job_id"], unique=False)

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=120), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("subtotal", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("due_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_invoices_job_id_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
    )
    op.create_index("ix_invoices_job_id", "invoices", ["job_id"], unique=False)
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"], unique=False)

    op.create_table(
        "agent_actions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action_type", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=120), nullable=True),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], name=op.f("fk_agent_actions_session_id_agent_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_actions")),
    )
    op.create_index("ix_agent_actions_session_id", "agent_actions", ["session_id"], unique=False)
    op.create_index("ix_agent_actions_timestamp", "agent_actions", ["timestamp"], unique=False)

    op.create_table(
        "invoice_items",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("type", sa.String(length=120), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], name=op.f("fk_invoice_items_invoice_id_invoices"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_invoice_items_job_id_jobs"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_items")),
    )
    op.create_index("ix_invoice_items_invoice_id", "invoice_items", ["invoice_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invoice_items_invoice_id", table_name="invoice_items")
    op.drop_table("invoice_items")

    op.drop_index("ix_agent_actions_timestamp", table_name="agent_actions")
    op.drop_index("ix_agent_actions_session_id", table_name="agent_actions")
    op.drop_table("agent_actions")

    op.drop_index("ix_invoices_invoice_number", table_name="invoices")
    op.drop_index("ix_invoices_job_id", table_name="invoices")
    op.drop_table("invoices")

    op.drop_index("ix_notes_job_id", table_name="notes")
    op.drop_table("notes")

    op.drop_index("ix_job_tags_job_id", table_name="job_tags")
    op.drop_table("job_tags")

    op.drop_index("ix_job_employees_employee_id", table_name="job_employees")
    op.drop_index("ix_job_employees_job_id", table_name="job_employees")
    op.drop_table("job_employees")

    op.drop_table("agent_sessions")

    op.drop_index("ix_jobs_city_state_zip", table_name="jobs")
    op.drop_index("ix_jobs_street", table_name="jobs")
    op.drop_index("ix_jobs_work_status", table_name="jobs")
    op.drop_index("ix_jobs_scheduled_start", table_name="jobs")
    op.drop_index("ix_jobs_invoice_number", table_name="jobs")
    op.drop_index("ix_jobs_customer_id", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_customer_addresses_city_state_zip", table_name="customer_addresses")
    op.drop_index("ix_customer_addresses_customer_id", table_name="customer_addresses")
    op.drop_table("customer_addresses")

    op.drop_table("employees")
    op.drop_table("customers")
