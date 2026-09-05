from __future__ import annotations

import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ingestion.backend import ensure_backend_path
from ingestion.pipeline import (
    IngestionValidationError,
    build_count_stages,
    build_employee_lookup,
    build_source_derived_counts,
    resolve_employee_id,
    run_ingestion,
    validate_count_stages,
)
from ingestion.source import load_source_data

ensure_backend_path()

from app.db.base import Base  # noqa: E402
from app.db.models import Customer, CustomerAddress, Employee, Invoice, InvoiceItem, Job, JobEmployee, JobTag, Note  # noqa: E402


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(
            self.engine,
            tables=[
                Customer.__table__,
                CustomerAddress.__table__,
                Employee.__table__,
                Job.__table__,
                JobEmployee.__table__,
                JobTag.__table__,
                Note.__table__,
                Invoice.__table__,
                InvoiceItem.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)

    def _build_source_data(self) -> dict[str, list[dict[str, str]]]:
        return {
            "customers": [
                {
                    "customer_id": "cus_1",
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "company": "",
                    "kind": "homeowner",
                    "job_count": "1",
                    "first_job": "2026-03-01T10:00:00Z",
                    "last_job": "2026-03-01T10:00:00Z",
                    "addresses": "100 Ocean Dr Miami FL 33101 | 200 Ocean Dr Miami FL 33101",
                }
            ],
            "employees": [
                {"id": "pro_1", "first_name": "Sam", "last_name": "Tech", "role": "field tech", "jobs": "1"}
            ],
            "jobs": [
                {
                    "job_id": "job_1",
                    "invoice_number": "5001",
                    "scheduled_start": "2026-03-01T10:00:00Z",
                    "scheduled_end": "2026-03-01T12:00:00Z",
                    "work_status": "complete rated",
                    "description": "Tune up",
                    "tags": "Maintenance; Membership",
                    "customer_id": "cus_1",
                    "customer_first": "Ada",
                    "customer_last": "Lovelace",
                    "customer_company": "",
                    "street": "100 Ocean Dr",
                    "unit": "Apt 2",
                    "city": "Miami",
                    "state": "FL",
                    "zip": "33101",
                    "techs": "Sam Tech",
                    "total_amount_usd": "123.4",
                    "outstanding_usd": "0.0",
                    "note_count": "1",
                    "notes": "denormalized note - should be ignored",
                }
            ],
            "notes": [
                {
                    "job_id": "job_1",
                    "invoice_number": "5001",
                    "note_index": "1",
                    "note_id": "nte_1",
                    "content": "canonical note",
                }
            ],
            "invoices": [
                {
                    "invoice_id": "invoice_1",
                    "job_id": "job_1",
                    "invoice_number": "5001",
                    "status": "open",
                    "amount_usd": "123.40",
                    "subtotal_usd": "123.40",
                    "due_usd": "123.40",
                    "paid_at": "",
                    "service_date": "2026-03-01T10:00:00Z",
                    "invoice_date": "2026-03-02T10:00:00Z",
                    "item_count": "1",
                }
            ],
            "invoice_items": [
                {
                    "invoice_id": "invoice_1",
                    "job_id": "job_1",
                    "invoice_number": "5001",
                    "item": "Labor",
                    "type": "labor",
                    "qty": "100",
                    "unit_price_usd": "123.40",
                    "amount_usd": "123.40",
                }
            ],
        }

    def _load_real_source_data(self) -> dict[str, list[dict[str, str]]]:
        repo_root = Path(__file__).resolve().parents[2]
        return load_source_data(repo_root / "data")

    def test_unique_employee_name_resolves_correctly(self) -> None:
        lookup = build_employee_lookup(self._build_source_data()["employees"])
        employee_id, issue = resolve_employee_id("Sam Tech", lookup)
        self.assertEqual(employee_id, "pro_1")
        self.assertIsNone(issue)

    def test_unknown_employee_name_raises_validation_issue(self) -> None:
        source_data = self._build_source_data()
        source_data["jobs"][0]["techs"] = "Unknown Tech"

        session = self.Session()
        with self.assertRaises(IngestionValidationError) as ctx:
            run_ingestion(session, source_data)
        session.close()

        self.assertTrue(any(issue.category == "unresolved_technician" for issue in ctx.exception.issues))

    def test_ambiguous_employee_name_raises_validation_issue(self) -> None:
        source_data = self._build_source_data()
        source_data["employees"].append(
            {"id": "pro_2", "first_name": "Sam", "last_name": "Tech", "role": "field tech", "jobs": "1"}
        )

        session = self.Session()
        with self.assertRaises(IngestionValidationError) as ctx:
            run_ingestion(session, source_data)
        session.close()

        categories = {issue.category for issue in ctx.exception.issues}
        self.assertIn("duplicate_employee_name", categories)
        self.assertIn("ambiguous_technician", categories)

    def test_count_validation_passes_when_source_parsed_database_match(self) -> None:
        count_stages = build_count_stages(
            {"customers": 1, "customer_addresses": 2, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
            {"customers": 1, "customer_addresses": 2, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
            {"customers": 1, "customer_addresses": 2, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
        )
        self.assertTrue(all(validate_count_stages(count_stages).values()))

    def test_count_validation_fails_when_source_differs_from_parsed(self) -> None:
        count_stages = build_count_stages(
            {"customers": 2, "customer_addresses": 2, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
            {"customers": 1, "customer_addresses": 2, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
            {"customers": 1, "customer_addresses": 2, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
        )
        checks = validate_count_stages(count_stages)
        self.assertFalse(checks["customers"])

    def test_count_validation_fails_when_parsed_differs_from_database(self) -> None:
        count_stages = build_count_stages(
            {"customers": 1, "customer_addresses": 2, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
            {"customers": 1, "customer_addresses": 2, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
            {"customers": 1, "customer_addresses": 1, "employees": 1, "jobs": 1, "job_employees": 1, "job_tags": 2, "notes": 1, "invoices": 1, "invoice_items": 1},
        )
        checks = validate_count_stages(count_stages)
        self.assertFalse(checks["customer_addresses"])

    def test_derived_relationship_count_uses_normalized_unique_relationships(self) -> None:
        source_data = self._build_source_data()
        source_data["jobs"][0]["techs"] = "Sam Tech; Sam Tech"
        source_data["jobs"][0]["tags"] = "Maintenance; maintenance; Membership"
        employee_lookup = build_employee_lookup(source_data["employees"])

        derived_counts = build_source_derived_counts(source_data, employee_lookup)

        self.assertEqual(derived_counts["job_employees"], 1)
        self.assertEqual(derived_counts["job_tags"], 2)

    def test_canonical_note_ingestion(self) -> None:
        source_data = self._build_source_data()
        session = self.Session()
        report = run_ingestion(session, source_data)
        self.assertEqual(report.db_counts["notes"], 1)
        self.assertTrue(report.validation_checks["notes_are_canonical"])
        self.assertTrue(report.validation_checks["count_stage_consistency"])
        session.close()

    def test_idempotent_ingestion(self) -> None:
        source_data = self._build_source_data()

        session1 = self.Session()
        report1 = run_ingestion(session1, source_data)
        session1.close()

        session2 = self.Session()
        report2 = run_ingestion(session2, source_data)
        session2.close()

        self.assertEqual(report1.db_counts, report2.db_counts)
        self.assertEqual(report2.db_counts["customers"], 1)
        self.assertEqual(report2.db_counts["customer_addresses"], 2)
        self.assertEqual(report2.db_counts["job_employees"], 1)
        self.assertEqual(report2.db_counts["job_tags"], 2)
        self.assertEqual(report2.db_counts["notes"], 1)
        self.assertEqual(report2.db_counts["invoice_items"], 1)

    def test_existing_real_dataset_passes_all_count_checks(self) -> None:
        source_data = self._load_real_source_data()

        session = self.Session()
        report = run_ingestion(session, source_data)
        session.close()

        self.assertTrue(report.validation_checks["technician_resolution"])
        self.assertTrue(report.validation_checks["count_stage_consistency"])
        self.assertTrue(report.validation_checks["expected_counts"])
        self.assertTrue(all(stage.matches for stage in report.count_stages.values()))
        self.assertEqual(report.count_stages["customers"].source, 732)
        self.assertEqual(report.count_stages["customers"].parsed, 732)
        self.assertEqual(report.count_stages["customers"].database, 732)


if __name__ == "__main__":
    unittest.main()
