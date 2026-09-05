"""Change imported source IDs from integer to text.

Revision ID: 20260903_02
Revises: 20260903_01
Create Date: 2026-09-03

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260903_02"
down_revision = "20260903_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fk_agent_sessions_customer_id_customers", "agent_sessions", type_="foreignkey")
    op.drop_constraint("fk_jobs_customer_id_customers", "jobs", type_="foreignkey")
    op.drop_constraint("fk_customer_addresses_customer_id_customers", "customer_addresses", type_="foreignkey")
    op.drop_constraint("fk_job_employees_job_id_jobs", "job_employees", type_="foreignkey")
    op.drop_constraint("fk_job_employees_employee_id_employees", "job_employees", type_="foreignkey")
    op.drop_constraint("fk_job_tags_job_id_jobs", "job_tags", type_="foreignkey")
    op.drop_constraint("fk_notes_job_id_jobs", "notes", type_="foreignkey")
    op.drop_constraint("fk_invoices_job_id_jobs", "invoices", type_="foreignkey")
    op.drop_constraint("fk_invoice_items_invoice_id_invoices", "invoice_items", type_="foreignkey")
    op.drop_constraint("fk_invoice_items_job_id_jobs", "invoice_items", type_="foreignkey")

    op.alter_column("customers", "id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="id::text")
    op.alter_column("customer_addresses", "id", existing_type=sa.Integer(), type_=sa.String(length=128), postgresql_using="id::text")
    op.alter_column("customer_addresses", "customer_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="customer_id::text")

    op.alter_column("employees", "id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="id::text")

    op.alter_column("jobs", "id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="id::text")
    op.alter_column("jobs", "customer_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="customer_id::text")

    op.alter_column("job_employees", "job_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="job_id::text")
    op.alter_column("job_employees", "employee_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="employee_id::text")

    op.alter_column("job_tags", "job_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="job_id::text")

    op.alter_column("notes", "id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="id::text")
    op.alter_column("notes", "job_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="job_id::text")

    op.alter_column("invoices", "id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="id::text")
    op.alter_column("invoices", "job_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="job_id::text")

    op.alter_column("invoice_items", "id", existing_type=sa.Integer(), type_=sa.String(length=128), postgresql_using="id::text")
    op.alter_column("invoice_items", "invoice_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="invoice_id::text")
    op.alter_column("invoice_items", "job_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="job_id::text")

    op.alter_column("agent_sessions", "customer_id", existing_type=sa.Integer(), type_=sa.String(length=64), postgresql_using="customer_id::text")

    op.create_foreign_key(
        "fk_agent_sessions_customer_id_customers",
        "agent_sessions",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_jobs_customer_id_customers",
        "jobs",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_customer_addresses_customer_id_customers",
        "customer_addresses",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_employees_job_id_jobs",
        "job_employees",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_employees_employee_id_employees",
        "job_employees",
        "employees",
        ["employee_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_tags_job_id_jobs",
        "job_tags",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_notes_job_id_jobs",
        "notes",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_invoices_job_id_jobs",
        "invoices",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_invoice_items_invoice_id_invoices",
        "invoice_items",
        "invoices",
        ["invoice_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_invoice_items_job_id_jobs",
        "invoice_items",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_sessions_customer_id_customers", "agent_sessions", type_="foreignkey")
    op.drop_constraint("fk_jobs_customer_id_customers", "jobs", type_="foreignkey")
    op.drop_constraint("fk_customer_addresses_customer_id_customers", "customer_addresses", type_="foreignkey")
    op.drop_constraint("fk_job_employees_job_id_jobs", "job_employees", type_="foreignkey")
    op.drop_constraint("fk_job_employees_employee_id_employees", "job_employees", type_="foreignkey")
    op.drop_constraint("fk_job_tags_job_id_jobs", "job_tags", type_="foreignkey")
    op.drop_constraint("fk_notes_job_id_jobs", "notes", type_="foreignkey")
    op.drop_constraint("fk_invoices_job_id_jobs", "invoices", type_="foreignkey")
    op.drop_constraint("fk_invoice_items_invoice_id_invoices", "invoice_items", type_="foreignkey")
    op.drop_constraint("fk_invoice_items_job_id_jobs", "invoice_items", type_="foreignkey")

    op.alter_column("agent_sessions", "customer_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="NULLIF(customer_id, '')::integer")

    op.alter_column("invoice_items", "job_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="NULLIF(job_id, '')::integer")
    op.alter_column("invoice_items", "invoice_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="invoice_id::integer")
    op.alter_column("invoice_items", "id", existing_type=sa.String(length=128), type_=sa.Integer(), postgresql_using="id::integer")

    op.alter_column("invoices", "job_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="job_id::integer")
    op.alter_column("invoices", "id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="id::integer")

    op.alter_column("notes", "job_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="job_id::integer")
    op.alter_column("notes", "id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="id::integer")

    op.alter_column("job_tags", "job_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="job_id::integer")

    op.alter_column("job_employees", "employee_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="employee_id::integer")
    op.alter_column("job_employees", "job_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="job_id::integer")

    op.alter_column("jobs", "customer_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="NULLIF(customer_id, '')::integer")
    op.alter_column("jobs", "id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="id::integer")

    op.alter_column("employees", "id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="id::integer")

    op.alter_column("customer_addresses", "customer_id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="customer_id::integer")
    op.alter_column("customer_addresses", "id", existing_type=sa.String(length=128), type_=sa.Integer(), postgresql_using="id::integer")
    op.alter_column("customers", "id", existing_type=sa.String(length=64), type_=sa.Integer(), postgresql_using="id::integer")

    op.create_foreign_key(
        "fk_agent_sessions_customer_id_customers",
        "agent_sessions",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_jobs_customer_id_customers",
        "jobs",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_customer_addresses_customer_id_customers",
        "customer_addresses",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_employees_job_id_jobs",
        "job_employees",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_employees_employee_id_employees",
        "job_employees",
        "employees",
        ["employee_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_tags_job_id_jobs",
        "job_tags",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_notes_job_id_jobs",
        "notes",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_invoices_job_id_jobs",
        "invoices",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_invoice_items_invoice_id_invoices",
        "invoice_items",
        "invoices",
        ["invoice_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_invoice_items_job_id_jobs",
        "invoice_items",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
