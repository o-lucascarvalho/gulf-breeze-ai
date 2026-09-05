from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Invoice, InvoiceItem


class InvoiceRepository:
    def get_latest_job_invoice(self, session: Session, job_id: str) -> Invoice | None:
        stmt = (
            select(Invoice)
            .where(Invoice.job_id == job_id)
            .order_by(
                Invoice.service_date.desc().nulls_last(),
                Invoice.invoice_date.desc().nulls_last(),
                Invoice.created_at.desc().nulls_last(),
                Invoice.id.asc(),
            )
            .limit(1)
        )
        return session.scalars(stmt).first()

    def get_invoice_items(self, session: Session, invoice_id: str) -> list[InvoiceItem]:
        stmt = (
            select(InvoiceItem)
            .where(InvoiceItem.invoice_id == invoice_id)
            .order_by(InvoiceItem.id.asc())
        )
        return list(session.scalars(stmt).all())
