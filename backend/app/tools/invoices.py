from __future__ import annotations

from pydantic import BaseModel

from app.repositories.invoice_repository import InvoiceRepository
from app.tools.helpers import to_jsonable
from app.tools.types import ToolDefinition


class GetJobInvoiceInput(BaseModel):
    job_id: str


def build_invoice_tools(session_factory) -> list[ToolDefinition]:
    repository = InvoiceRepository()

    def get_job_invoice(args: GetJobInvoiceInput) -> dict:
        with session_factory() as session:
            invoice = repository.get_latest_job_invoice(session, args.job_id)
            if invoice is None:
                return {"found": False, "job_id": args.job_id, "invoice": None}

            line_items = repository.get_invoice_items(session, invoice.id)

        return {
            "found": True,
            "job_id": args.job_id,
            "invoice": to_jsonable(
                {
                    "invoice_id": invoice.id,
                    "invoice_number": invoice.invoice_number,
                    "status": invoice.status,
                    "subtotal": invoice.subtotal,
                    "amount": invoice.amount,
                    "due_amount": invoice.due_amount,
                    "service_date": invoice.service_date,
                    "invoice_date": invoice.invoice_date,
                    "paid_at": invoice.paid_at,
                    "sent_at": invoice.sent_at,
                    "line_items": [
                        {
                            "line_item_id": item.id,
                            "name": item.name,
                            "type": item.type,
                            "unit_price": item.unit_price,
                            "quantity": item.quantity,
                            "amount": item.amount,
                        }
                        for item in line_items
                    ],
                }
            ),
        }

    return [
        ToolDefinition(
            name="get_job_invoice",
            description=(
                "Get the latest invoice and line items for a job_id. Use this for charge details, billed items, and "
                "invoice status questions."
            ),
            input_model=GetJobInvoiceInput,
            handler=get_job_invoice,
        )
    ]
