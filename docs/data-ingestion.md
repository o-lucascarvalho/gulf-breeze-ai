# V1.2 Data Ingestion

This document describes the V1.2 ingestion pipeline for Gulf Breeze Air.

## Scope

- Source: CSV files in `data/`
- Target: normalized PostgreSQL schema
- Output: validation report

Out of scope in V1.2:

- embeddings, pgvector, RAG
- agent/tool orchestration
- frontend/voice integrations

## Source Files

- `customers.csv`
- `employees.csv`
- `jobs.csv`
- `notes.csv`
- `invoices.csv`
- `invoice_items.csv`

CSV is the canonical source format for this phase.

## Canonical Mapping Rules

- `customers.csv` maps to `customers` plus normalized `customer_addresses`.
- Pipe-separated customer addresses are parsed and inserted as one row per address.
- `employees.csv` maps to `employees` and preserves source IDs.
- `jobs.csv` maps to `jobs`.
- `jobs.techs` is parsed into `job_employees`; unresolved technicians fail validation.
- `jobs.tags` is parsed into `job_tags`.
- `notes.csv` is the canonical note source and maps to `notes`.
- Denormalized `jobs.notes` is intentionally ignored.
- `invoices.csv` maps to `invoices`.
- `invoice_items.csv` maps to `invoice_items`.
- Monetary values are parsed with Decimal and persisted to exact numeric columns.

## Idempotency

Ingestion performs deterministic upsert behavior keyed by source IDs and composite keys.

Running ingestion repeatedly with the same source data does not increase counts.

## Validation

Pipeline validation includes:

- source-level duplicate ID checks
- source reference checks before ingest
- database row counts per table
- referential integrity checks after ingest
- duplicate relationship checks
- canonical note-count check
- monetary and quantity aggregate checks

## CLI

From repository root:

```bash
backend/.venv/bin/python -m ingestion.scripts.ingest --data-dir data
```

The command prints source counts, database counts, and validation status.
