from __future__ import annotations

import csv
from pathlib import Path

REQUIRED_FILES = {
    "customers": "customers.csv",
    "employees": "employees.csv",
    "jobs": "jobs.csv",
    "notes": "notes.csv",
    "invoices": "invoices.csv",
    "invoice_items": "invoice_items.csv",
}


class SourceDataError(RuntimeError):
    pass


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def load_source_data(data_dir: Path) -> dict[str, list[dict[str, str]]]:
    if not data_dir.exists() or not data_dir.is_dir():
        raise SourceDataError(f"Data directory not found: {data_dir}")

    data: dict[str, list[dict[str, str]]] = {}
    for key, filename in REQUIRED_FILES.items():
        file_path = data_dir / filename
        if not file_path.exists():
            raise SourceDataError(f"Required source file missing: {file_path}")
        data[key] = read_csv_rows(file_path)
    return data


def source_counts(source_data: dict[str, list[dict[str, str]]]) -> dict[str, int]:
    return {key: len(rows) for key, rows in source_data.items()}
