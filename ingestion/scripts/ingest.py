from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ingestion.backend import ensure_backend_path
from ingestion.pipeline import IngestionValidationError, run_ingestion
from ingestion.source import load_source_data

ensure_backend_path()

from app.db.session import SessionLocal  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gulf Breeze Air data ingestion")
    parser.add_argument("--data-dir", default="data", help="Path to source data directory")
    return parser.parse_args()


def _print_report(report) -> None:
    print("Gulf Breeze Air - Data Ingestion")
    print("")
    print("Source counts:")
    print(f"customers           {report.source_counts['customers']}")
    print(f"employees           {report.source_counts['employees']}")
    print(f"jobs                {report.source_counts['jobs']}")
    print(f"notes               {report.source_counts['notes']}")
    print(f"invoices            {report.source_counts['invoices']}")
    print(f"invoice_items       {report.source_counts['invoice_items']}")
    print("")
    print("Count validation:")
    ordered_entities = [
        "customers",
        "customer_addresses",
        "employees",
        "jobs",
        "job_employees",
        "job_tags",
        "notes",
        "invoices",
        "invoice_items",
    ]
    for entity in ordered_entities:
        stage = report.count_stages[entity]
        status = "OK" if stage.matches else "FAIL"
        print(f"{entity}")
        print(f"  {stage.source_label:<14} {stage.source}")
        print(f"  {'parsed':<14} {stage.parsed}")
        print(f"  {'database':<14} {stage.database}")
        print(f"  {status}")
    print("")
    print("Database counts:")
    print(f"customers           {report.db_counts['customers']}")
    print(f"customer_addresses  {report.db_counts['customer_addresses']}")
    print(f"employees           {report.db_counts['employees']}")
    print(f"jobs                {report.db_counts['jobs']}")
    print(f"job_employees       {report.db_counts['job_employees']}")
    print(f"job_tags            {report.db_counts['job_tags']}")
    print(f"notes               {report.db_counts['notes']}")
    print(f"invoices            {report.db_counts['invoices']}")
    print(f"invoice_items       {report.db_counts['invoice_items']}")
    print("")
    print("Validation:")
    for check_name, passed in report.validation_checks.items():
        marker = "OK" if passed else "FAIL"
        print(f"{marker:4} {check_name}")
    print(f"OK   quantity_mode={report.quantity_mode}")


def main() -> int:
    args = _parse_args()
    data_dir = Path(args.data_dir)

    try:
        source_data = load_source_data(data_dir)
    except Exception as exc:
        print(f"Error loading source files: {exc}", file=sys.stderr)
        return 1

    session = SessionLocal()
    try:
        report = run_ingestion(session, source_data)
    except IngestionValidationError as exc:
        print("Validation failed before ingestion:", file=sys.stderr)
        for issue in exc.issues[:50]:
            print(f"- [{issue.category}] {issue.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()

    _print_report(report)
    print("")
    if report.success:
        print("Ingestion completed successfully.")
        return 0

    print("Ingestion completed with validation failures.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
