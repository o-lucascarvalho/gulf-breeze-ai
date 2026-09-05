from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Customer, CustomerAddress, Employee, Invoice, InvoiceItem, Job, JobEmployee, JobScheduleOverride, JobTag, Note
from app.tools.customers import build_customer_tools
from app.tools.invoices import build_invoice_tools
from app.tools.jobs import build_job_tools
from app.tools.notes import build_note_tools
from app.tools.registry import ToolRegistry
from app.tools.semantic import build_semantic_tool
from retrieval.types import SemanticSearchResult


@dataclass
class FakeSemanticRetriever:
    results: list[SemanticSearchResult]
    calls: int = 0
    last_query: str | None = None
    last_top_k: int | None = None
    last_customer_id: str | None = None
    last_job_id: str | None = None

    def search(self, query: str, *, top_k: int = 10, customer_id: str | None = None, job_id: str | None = None):
        self.calls += 1
        self.last_query = query
        self.last_top_k = top_k
        self.last_customer_id = customer_id
        self.last_job_id = job_id
        return self.results


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(
            self.engine,
            tables=[
                Customer.__table__,
                CustomerAddress.__table__,
                Employee.__table__,
                Job.__table__,
                JobScheduleOverride.__table__,
                JobEmployee.__table__,
                JobTag.__table__,
                Invoice.__table__,
                InvoiceItem.__table__,
                Note.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)
        now = datetime.now(timezone.utc)

        with self.Session() as session:
            session.add_all(
                [
                    Customer(id="cus_1", first_name="John", last_name="Smith", company="Smith Home", kind="residential"),
                    Customer(id="cus_2", first_name="JOHN", last_name="SMITH", company="Smith Office", kind="commercial"),
                    Customer(id="cus_3", first_name="Maria", last_name="Garcia", company="Garcia Family", kind="residential"),
                    CustomerAddress(
                        id="addr_1",
                        customer_id="cus_1",
                        street="100 Main St",
                        city="Gulf Breeze",
                        state="FL",
                        zip="32561",
                    ),
                    CustomerAddress(
                        id="addr_2",
                        customer_id="cus_1",
                        street="101 Main St",
                        city="Gulf Breeze",
                        state="FL",
                        zip="32561",
                    ),
                    Employee(id="emp_1", first_name="Alex", last_name="Brown", role="Technician"),
                    Employee(id="emp_2", first_name="Jamie", last_name="Clark", role="Installer"),
                    Job(
                        id="job_1",
                        customer_id="cus_1",
                        description="Past service",
                        work_status="completed",
                        scheduled_start=now - timedelta(days=10),
                        scheduled_end=now - timedelta(days=10, hours=-1),
                        invoice_number="INV-1",
                        total_amount=Decimal("350.00"),
                        outstanding_balance=Decimal("25.00"),
                        street="100 Main St",
                        city="Gulf Breeze",
                        state="FL",
                        zip="32561",
                    ),
                    Job(
                        id="job_2",
                        customer_id="cus_1",
                        description="Future maintenance",
                        work_status="scheduled",
                        scheduled_start=now + timedelta(days=2),
                        scheduled_end=now + timedelta(days=2, hours=1),
                        invoice_number="INV-2",
                        total_amount=Decimal("200.00"),
                        outstanding_balance=Decimal("0.00"),
                        street="100 Main St",
                        city="Gulf Breeze",
                        state="FL",
                        zip="32561",
                    ),
                    Job(
                        id="job_3",
                        customer_id="cus_1",
                        description="New issue",
                        work_status="scheduled",
                        scheduled_start=now + timedelta(days=1),
                        scheduled_end=now + timedelta(days=1, hours=1),
                        invoice_number="INV-3",
                        total_amount=Decimal("99.00"),
                        outstanding_balance=Decimal("10.00"),
                        street="100 Main St",
                        city="Gulf Breeze",
                        state="FL",
                        zip="32561",
                    ),
                    Job(
                        id="job_4",
                        customer_id="cus_3",
                        description="Other customer job",
                        work_status="completed",
                        scheduled_start=now - timedelta(days=3),
                        scheduled_end=now - timedelta(days=3, hours=-1),
                        invoice_number="INV-4",
                        total_amount=Decimal("145.00"),
                        outstanding_balance=Decimal("0.00"),
                    ),
                    JobEmployee(job_id="job_1", employee_id="emp_1"),
                    JobEmployee(job_id="job_1", employee_id="emp_2"),
                    JobTag(job_id="job_1", tag="compressor"),
                    Invoice(
                        id="inv_1",
                        job_id="job_1",
                        invoice_number="INV-1",
                        status="paid",
                        subtotal=Decimal("300.00"),
                        amount=Decimal("350.00"),
                        due_amount=Decimal("0.00"),
                    ),
                    Invoice(
                        id="inv_2",
                        job_id="job_3",
                        invoice_number="INV-3",
                        status="open",
                        subtotal=Decimal("80.00"),
                        amount=Decimal("99.00"),
                        due_amount=Decimal("10.00"),
                    ),
                    InvoiceItem(
                        id="item_1",
                        invoice_id="inv_1",
                        job_id="job_1",
                        name="Compressor",
                        type="part",
                        unit_price=Decimal("200.00"),
                        quantity=Decimal("1.00"),
                        amount=Decimal("200.00"),
                    ),
                    InvoiceItem(
                        id="item_2",
                        invoice_id="inv_1",
                        job_id="job_1",
                        name="Labor",
                        type="labor",
                        unit_price=Decimal("100.00"),
                        quantity=Decimal("1.00"),
                        amount=Decimal("100.00"),
                    ),
                    Note(id="nte_1", job_id="job_1", note_index=1, content="compressor failure observed"),
                    Note(id="nte_2", job_id="job_3", note_index=1, content="cooling complaint"),
                ]
            )
            session.commit()

        fake_semantic = FakeSemanticRetriever(
            results=[
                SemanticSearchResult(
                    note_id="nte_1",
                    job_id="job_1",
                    customer_id="cus_1",
                    content="compressor failure observed",
                    similarity_score=0.9,
                ),
                SemanticSearchResult(
                    note_id="nte_2",
                    job_id="job_3",
                    customer_id="cus_1",
                    content="cooling complaint",
                    similarity_score=0.8,
                ),
            ]
        )
        self.fake_semantic = fake_semantic

        tools = []
        tools.extend(build_customer_tools(self.Session))
        tools.extend(build_job_tools(self.Session))
        tools.extend(build_invoice_tools(self.Session))
        tools.extend(build_note_tools(self.Session))
        tools.append(build_semantic_tool(fake_semantic))
        self.registry = ToolRegistry(tools)

    def test_search_customers_unique_match(self) -> None:
        result = self.registry.execute("search_customers", {"name": "Maria Garcia"}).payload
        self.assertTrue(result["ok"])
        matches = result["data"]["matches"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["customer_id"], "cus_3")

    def test_search_customers_no_match(self) -> None:
        result = self.registry.execute("search_customers", {"name": "Nobody"}).payload
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["matches"], [])

    def test_search_customers_ambiguous_case_insensitive_and_bound(self) -> None:
        result = self.registry.execute("search_customers", {"name": "john smith", "limit": 1}).payload
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["matches"]), 1)

        result2 = self.registry.execute("search_customers", {"name": "JOHN SMITH", "limit": 10}).payload
        self.assertTrue(result2["ok"])
        self.assertGreaterEqual(len(result2["data"]["matches"]), 2)
        self.assertTrue(result2["data"]["ambiguous"])

    def test_get_customer_valid_and_unknown(self) -> None:
        found = self.registry.execute("get_customer", {"customer_id": "cus_1"}).payload
        self.assertTrue(found["ok"])
        self.assertTrue(found["data"]["found"])
        self.assertEqual(len(found["data"]["customer"]["addresses"]), 2)

        missing = self.registry.execute("get_customer", {"customer_id": "missing"}).payload
        self.assertTrue(missing["ok"])
        self.assertFalse(missing["data"]["found"])

    def test_get_customer_jobs_filters_customer_order_limit_and_status(self) -> None:
        result = self.registry.execute("get_customer_jobs", {"customer_id": "cus_1", "limit": 2}).payload
        self.assertTrue(result["ok"])
        jobs = result["data"]["jobs"]
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["job_id"], "job_2")
        self.assertEqual(jobs[1]["job_id"], "job_3")

        scheduled = self.registry.execute(
            "get_customer_jobs", {"customer_id": "cus_1", "status": "SCHEDULED", "limit": 10}
        ).payload
        self.assertTrue(scheduled["ok"])
        self.assertTrue(all(job["work_status"].lower() == "scheduled" for job in scheduled["data"]["jobs"]))

    def test_get_customer_balance_decimal_aggregation_zero_and_unknown(self) -> None:
        result = self.registry.execute("get_customer_balance", {"customer_id": "cus_1"}).payload
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["total_outstanding_balance"], "35.00")
        self.assertEqual(result["data"]["open_jobs_or_invoices"], 2)

        zero = self.registry.execute("get_customer_balance", {"customer_id": "cus_3"}).payload
        self.assertEqual(zero["data"]["total_outstanding_balance"], "0.00")

        missing = self.registry.execute("get_customer_balance", {"customer_id": "missing"}).payload
        self.assertFalse(missing["data"]["found"])

    def test_get_upcoming_appointments_future_only_and_ordered(self) -> None:
        result = self.registry.execute("get_upcoming_appointments", {"customer_id": "cus_1", "limit": 10}).payload
        self.assertTrue(result["ok"])
        items = result["data"]["appointments"]
        self.assertEqual([row["job_id"] for row in items], ["job_3", "job_2"])

    def test_get_job_valid_and_invalid(self) -> None:
        result = self.registry.execute("get_job", {"job_id": "job_1"}).payload
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["found"])
        self.assertEqual(result["data"]["job"]["job_id"], "job_1")
        self.assertEqual(len(result["data"]["job"]["employees"]), 2)

        missing = self.registry.execute("get_job", {"job_id": "missing"}).payload
        self.assertTrue(missing["ok"])
        self.assertFalse(missing["data"]["found"])

    def test_get_job_invoice_with_items_and_without_invoice(self) -> None:
        result = self.registry.execute("get_job_invoice", {"job_id": "job_1"}).payload
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["found"])
        self.assertEqual(len(result["data"]["invoice"]["line_items"]), 2)

        missing = self.registry.execute("get_job_invoice", {"job_id": "job_2"}).payload
        self.assertTrue(missing["ok"])
        self.assertFalse(missing["data"]["found"])

    def test_get_note_valid(self) -> None:
        result = self.registry.execute("get_note", {"note_id": "nte_1"}).payload
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["note_id"], "nte_1")
        self.assertEqual(result["data"]["job_id"], "job_1")
        self.assertEqual(result["data"]["customer_id"], "cus_1")
        self.assertEqual(result["data"]["content"], "compressor failure observed")

    def test_get_note_missing(self) -> None:
        result = self.registry.execute("get_note", {"note_id": "missing"}).payload
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "note_not_found")

    def test_semantic_search_notes_delegates_forwards_filters_and_preserves_order(self) -> None:
        result = self.registry.execute(
            "semantic_search_notes",
            {"query": "compressor issue", "top_k": 5, "customer_id": "cus_1", "job_id": "job_1"},
        ).payload
        self.assertTrue(result["ok"])
        self.assertEqual(self.fake_semantic.calls, 1)
        self.assertEqual(self.fake_semantic.last_query, "compressor issue")
        self.assertEqual(self.fake_semantic.last_top_k, 5)
        self.assertEqual(self.fake_semantic.last_customer_id, "cus_1")
        self.assertEqual(self.fake_semantic.last_job_id, "job_1")
        rows = result["data"]["results"]
        self.assertEqual([row["note_id"] for row in rows], ["nte_1", "nte_2"])


if __name__ == "__main__":
    unittest.main()
