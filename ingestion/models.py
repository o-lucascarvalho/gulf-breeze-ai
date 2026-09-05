from __future__ import annotations

from ingestion.backend import ensure_backend_path

ensure_backend_path()

from app.db.models import (  # noqa: E402
    Customer,
    CustomerAddress,
    Employee,
    Invoice,
    InvoiceItem,
    Job,
    JobEmployee,
    JobTag,
    Note,
)

INGESTION_TABLE_MODELS = {
    "customers": Customer,
    "customer_addresses": CustomerAddress,
    "employees": Employee,
    "jobs": Job,
    "job_employees": JobEmployee,
    "job_tags": JobTag,
    "notes": Note,
    "invoices": Invoice,
    "invoice_items": InvoiceItem,
}
