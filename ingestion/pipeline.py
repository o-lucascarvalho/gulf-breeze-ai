from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ingestion.models import (
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
from ingestion.parsers import (
    build_customer_address_id,
    build_invoice_item_id,
    clean_text,
    detect_quantity_mode,
    normalize_key,
    parse_address_components,
    parse_int,
    parse_iso_date,
    parse_iso_datetime,
    parse_money,
    parse_quantity,
    split_pipe_addresses,
    split_semicolon_list,
)
from ingestion.source import source_counts
from ingestion.types import CountStage, IngestionReport, ValidationIssue


class IngestionValidationError(RuntimeError):
    def __init__(self, issues: list[ValidationIssue]):
        super().__init__("Source validation failed")
        self.issues = issues


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = normalize_key(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _parse_customers(customers_rows: list[dict[str, str]], city_candidates: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    customers_payload: list[dict[str, Any]] = []
    addresses_payload: list[dict[str, Any]] = []

    for row in customers_rows:
        customer_id = clean_text(row.get("customer_id"))
        if not customer_id:
            continue

        customers_payload.append(
            {
                "id": customer_id,
                "first_name": clean_text(row.get("first_name")),
                "last_name": clean_text(row.get("last_name")),
                "company": clean_text(row.get("company")),
                "kind": clean_text(row.get("kind")),
                "job_count": parse_int(row.get("job_count")),
                "first_job": parse_iso_datetime(row.get("first_job")),
                "last_job": parse_iso_datetime(row.get("last_job")),
                "created_at": None,
                "updated_at": None,
            }
        )

        seen_addresses: set[str] = set()
        for address in split_pipe_addresses(row.get("addresses")):
            normalized_key = normalize_key(address)
            if normalized_key in seen_addresses:
                continue
            seen_addresses.add(normalized_key)

            parsed = parse_address_components(address, city_candidates)
            address_id = build_customer_address_id(customer_id, parsed["normalized_address"] or address)
            addresses_payload.append(
                {
                    "id": address_id,
                    "customer_id": customer_id,
                    "street": parsed["street"],
                    "street_line_2": parsed["street_line_2"],
                    "city": parsed["city"],
                    "state": parsed["state"],
                    "zip": parsed["zip"],
                    "latitude": None,
                    "longitude": None,
                    "normalized_address": parsed["normalized_address"],
                    "created_at": None,
                    "updated_at": None,
                }
            )

    return customers_payload, addresses_payload


def build_employee_lookup(employee_rows: list[dict[str, str]]) -> dict[str, list[str]]:
    employee_lookup: dict[str, list[str]] = {}

    for row in employee_rows:
        employee_id = clean_text(row.get("id"))
        if not employee_id:
            continue

        first_name = clean_text(row.get("first_name"))
        last_name = clean_text(row.get("last_name"))
        full_name = " ".join(part for part in [first_name, last_name] if part)
        if not full_name:
            continue

        normalized_name = normalize_key(full_name)
        employee_lookup.setdefault(normalized_name, []).append(employee_id)

    return employee_lookup


def _validate_employee_lookup(employee_lookup: dict[str, list[str]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for normalized_name, employee_ids in sorted(employee_lookup.items()):
        unique_ids = sorted(set(employee_ids))
        if len(unique_ids) > 1:
            issues.append(
                ValidationIssue(
                    category="duplicate_employee_name",
                    message=(
                        f"employees.csv normalized name '{normalized_name}' maps to multiple employee IDs: "
                        f"{', '.join(unique_ids)}"
                    ),
                )
            )
    return issues


def _parse_employees(employee_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    employees_payload: list[dict[str, Any]] = []
    employee_lookup = build_employee_lookup(employee_rows)

    for row in employee_rows:
        employee_id = clean_text(row.get("id"))
        if not employee_id:
            continue

        first_name = clean_text(row.get("first_name"))
        last_name = clean_text(row.get("last_name"))
        employees_payload.append(
            {
                "id": employee_id,
                "first_name": first_name,
                "last_name": last_name,
                "role": clean_text(row.get("role")),
                "job_count": parse_int(row.get("jobs")),
                "created_at": None,
                "updated_at": None,
            }
        )

    return employees_payload, employee_lookup


def resolve_employee_id(tech_name: str, employee_lookup: dict[str, list[str]]) -> tuple[str | None, ValidationIssue | None]:
    normalized_name = normalize_key(tech_name)
    matches = sorted(set(employee_lookup.get(normalized_name, [])))

    if not matches:
        return None, ValidationIssue(
            category="unresolved_technician",
            message=f"technician '{tech_name}' not found in employees.csv",
        )

    if len(matches) > 1:
        return None, ValidationIssue(
            category="ambiguous_technician",
            message=f"technician '{tech_name}' is ambiguous across employee IDs: {', '.join(matches)}",
        )

    return matches[0], None


def _parse_jobs_and_relations(
    jobs_rows: list[dict[str, str]],
    employee_lookup: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[ValidationIssue]]:
    jobs_payload: list[dict[str, Any]] = []
    job_employees_payload: list[dict[str, Any]] = []
    job_tags_payload: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []

    for row in jobs_rows:
        job_id = clean_text(row.get("job_id"))
        if not job_id:
            continue

        jobs_payload.append(
            {
                "id": job_id,
                "invoice_number": clean_text(row.get("invoice_number")),
                "customer_id": clean_text(row.get("customer_id")),
                "description": clean_text(row.get("description")),
                "work_status": clean_text(row.get("work_status")),
                "scheduled_start": parse_iso_datetime(row.get("scheduled_start")),
                "scheduled_end": parse_iso_datetime(row.get("scheduled_end")),
                "time_zone": "UTC",
                "arrival_window_minutes": None,
                "on_my_way_at": None,
                "started_at": None,
                "completed_at": None,
                "total_amount": parse_money(row.get("total_amount_usd")),
                "outstanding_balance": parse_money(row.get("outstanding_usd")),
                "street": clean_text(row.get("street")),
                "street_line_2": clean_text(row.get("unit")),
                "city": clean_text(row.get("city")),
                "state": clean_text(row.get("state")),
                "zip": clean_text(row.get("zip")),
                "latitude": None,
                "longitude": None,
                "created_at": None,
                "updated_at": None,
            }
        )

        technician_names = _dedupe_preserve_order(split_semicolon_list(row.get("techs")))
        for tech_name in technician_names:
            employee_id, resolution_issue = resolve_employee_id(tech_name, employee_lookup)
            if resolution_issue is not None:
                issues.append(
                    ValidationIssue(
                        category=resolution_issue.category,
                        message=f"job {job_id}: {resolution_issue.message}",
                    )
                )
                continue
            job_employees_payload.append({"job_id": job_id, "employee_id": employee_id})

        tags = _dedupe_preserve_order(split_semicolon_list(row.get("tags")))
        for tag in tags:
            job_tags_payload.append({"job_id": job_id, "tag": tag})

    return jobs_payload, job_employees_payload, job_tags_payload, issues


def _parse_notes(notes_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in notes_rows:
        note_id = clean_text(row.get("note_id"))
        job_id = clean_text(row.get("job_id"))
        if not note_id or not job_id:
            continue
        payload.append(
            {
                "id": note_id,
                "job_id": job_id,
                "note_index": parse_int(row.get("note_index")) or 0,
                "content": row.get("content") or "",
                "created_at": None,
                "updated_at": None,
            }
        )
    return payload


def _parse_invoices(invoices_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for row in invoices_rows:
        invoice_id = clean_text(row.get("invoice_id"))
        job_id = clean_text(row.get("job_id"))
        if not invoice_id or not job_id:
            continue

        payload.append(
            {
                "id": invoice_id,
                "job_id": job_id,
                "invoice_number": clean_text(row.get("invoice_number")),
                "status": clean_text(row.get("status")),
                "amount": parse_money(row.get("amount_usd")),
                "subtotal": parse_money(row.get("subtotal_usd")),
                "due_amount": parse_money(row.get("due_usd")),
                "paid_at": parse_iso_datetime(row.get("paid_at")),
                "sent_at": None,
                "service_date": parse_iso_date(row.get("service_date")),
                "invoice_date": parse_iso_date(row.get("invoice_date")),
                "created_at": None,
                "updated_at": None,
            }
        )
    return payload


def _parse_invoice_items(invoice_items_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], str]:
    quantities = [row.get("qty", "") for row in invoice_items_rows]
    quantity_mode = detect_quantity_mode(quantities)

    payload: list[dict[str, Any]] = []
    for index, row in enumerate(invoice_items_rows, start=1):
        invoice_id = clean_text(row.get("invoice_id"))
        if not invoice_id:
            continue

        payload.append(
            {
                "id": build_invoice_item_id(invoice_id, index),
                "invoice_id": invoice_id,
                "job_id": clean_text(row.get("job_id")),
                "name": clean_text(row.get("item")),
                "type": clean_text(row.get("type")),
                "quantity": parse_quantity(row.get("qty"), quantity_mode),
                "unit_price": parse_money(row.get("unit_price_usd")),
                "amount": parse_money(row.get("amount_usd")),
                "created_at": None,
                "updated_at": None,
            }
        )

    return payload, quantity_mode


def _detect_duplicate_ids(rows: list[dict[str, str]], source_name: str, id_column: str) -> list[ValidationIssue]:
    seen: set[str] = set()
    dupes: list[ValidationIssue] = []
    for row in rows:
        value = clean_text(row.get(id_column))
        if not value:
            continue
        if value in seen:
            dupes.append(ValidationIssue(category="duplicate_source_id", message=f"{source_name}.{id_column} duplicate: {value}"))
        seen.add(value)
    return dupes


def _source_reference_checks(source_data: dict[str, list[dict[str, str]]]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    customer_ids = {clean_text(row.get("customer_id")) for row in source_data["customers"]}
    customer_ids.discard(None)

    employee_ids = {clean_text(row.get("id")) for row in source_data["employees"]}
    employee_ids.discard(None)

    job_ids = {clean_text(row.get("job_id")) for row in source_data["jobs"]}
    job_ids.discard(None)

    invoice_ids = {clean_text(row.get("invoice_id")) for row in source_data["invoices"]}
    invoice_ids.discard(None)

    issues.extend(_detect_duplicate_ids(source_data["customers"], "customers", "customer_id"))
    issues.extend(_detect_duplicate_ids(source_data["employees"], "employees", "id"))
    issues.extend(_detect_duplicate_ids(source_data["jobs"], "jobs", "job_id"))
    issues.extend(_detect_duplicate_ids(source_data["notes"], "notes", "note_id"))
    issues.extend(_detect_duplicate_ids(source_data["invoices"], "invoices", "invoice_id"))
    issues.extend(_validate_employee_lookup(build_employee_lookup(source_data["employees"])))

    for row in source_data["jobs"]:
        job_id = clean_text(row.get("job_id"))
        customer_id = clean_text(row.get("customer_id"))
        if customer_id and customer_id not in customer_ids:
            issues.append(ValidationIssue(category="missing_customer_reference", message=f"jobs.job_id={job_id} references unknown customer_id={customer_id}"))

    for row in source_data["notes"]:
        note_id = clean_text(row.get("note_id"))
        job_id = clean_text(row.get("job_id"))
        if job_id and job_id not in job_ids:
            issues.append(ValidationIssue(category="missing_job_reference", message=f"notes.note_id={note_id} references unknown job_id={job_id}"))

    for row in source_data["invoices"]:
        invoice_id = clean_text(row.get("invoice_id"))
        job_id = clean_text(row.get("job_id"))
        if job_id and job_id not in job_ids:
            issues.append(ValidationIssue(category="missing_job_reference", message=f"invoices.invoice_id={invoice_id} references unknown job_id={job_id}"))

    for row in source_data["invoice_items"]:
        invoice_id = clean_text(row.get("invoice_id"))
        job_id = clean_text(row.get("job_id"))
        if invoice_id and invoice_id not in invoice_ids:
            issues.append(ValidationIssue(category="missing_invoice_reference", message=f"invoice_items references unknown invoice_id={invoice_id}"))
        if job_id and job_id not in job_ids:
            issues.append(ValidationIssue(category="missing_job_reference", message=f"invoice_items references unknown job_id={job_id}"))

    if len(employee_ids) == 0:
        issues.append(ValidationIssue(category="empty_source", message="employees.csv contains no employee IDs"))

    return issues


def build_source_derived_counts(
    source_data: dict[str, list[dict[str, str]]],
    employee_lookup: dict[str, list[str]],
) -> dict[str, int]:
    customer_addresses = 0
    for row in source_data["customers"]:
        customer_addresses += len(
            {
                normalize_key(address)
                for address in split_pipe_addresses(row.get("addresses"))
            }
        )

    job_employees = 0
    for row in source_data["jobs"]:
        relationships: set[tuple[str, str]] = set()
        job_id = clean_text(row.get("job_id"))
        if not job_id:
            continue
        for tech_name in _dedupe_preserve_order(split_semicolon_list(row.get("techs"))):
            employee_id, resolution_issue = resolve_employee_id(tech_name, employee_lookup)
            if resolution_issue is None and employee_id is not None:
                relationships.add((job_id, employee_id))
        job_employees += len(relationships)

    job_tags = 0
    for row in source_data["jobs"]:
        job_tags += len(
            {
                normalize_key(tag)
                for tag in split_semicolon_list(row.get("tags"))
            }
        )

    return {
        "customers": len(source_data["customers"]),
        "customer_addresses": customer_addresses,
        "employees": len(source_data["employees"]),
        "jobs": len(source_data["jobs"]),
        "job_employees": job_employees,
        "job_tags": job_tags,
        "notes": len(source_data["notes"]),
        "invoices": len(source_data["invoices"]),
        "invoice_items": len(source_data["invoice_items"]),
    }


def build_parsed_counts(
    customers_payload: list[dict[str, Any]],
    addresses_payload: list[dict[str, Any]],
    employees_payload: list[dict[str, Any]],
    jobs_payload: list[dict[str, Any]],
    job_employees_payload: list[dict[str, Any]],
    job_tags_payload: list[dict[str, Any]],
    notes_payload: list[dict[str, Any]],
    invoices_payload: list[dict[str, Any]],
    invoice_items_payload: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        "customers": len(customers_payload),
        "customer_addresses": len(addresses_payload),
        "employees": len(employees_payload),
        "jobs": len(jobs_payload),
        "job_employees": len({(row["job_id"], row["employee_id"]) for row in job_employees_payload}),
        "job_tags": len({(row["job_id"], normalize_key(row["tag"])) for row in job_tags_payload}),
        "notes": len(notes_payload),
        "invoices": len(invoices_payload),
        "invoice_items": len(invoice_items_payload),
    }


def build_count_stages(
    source_derived_counts: dict[str, int],
    parsed_counts: dict[str, int],
    db_counts: dict[str, int],
) -> dict[str, CountStage]:
    derived_entities = {"customer_addresses", "job_employees", "job_tags"}
    return {
        entity: CountStage(
            source_label="source-derived" if entity in derived_entities else "source",
            source=source_derived_counts[entity],
            parsed=parsed_counts[entity],
            database=db_counts[entity],
        )
        for entity in parsed_counts
    }


def validate_count_stages(count_stages: dict[str, CountStage]) -> dict[str, bool]:
    return {entity: stage.matches for entity, stage in count_stages.items()}


def _upsert_rows(session: Session, model: Any, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        session.merge(model(**row))


def _fetch_db_counts(session: Session) -> dict[str, int]:
    return {
        "customers": session.scalar(select(func.count()).select_from(Customer)) or 0,
        "customer_addresses": session.scalar(select(func.count()).select_from(CustomerAddress)) or 0,
        "employees": session.scalar(select(func.count()).select_from(Employee)) or 0,
        "jobs": session.scalar(select(func.count()).select_from(Job)) or 0,
        "job_employees": session.scalar(select(func.count()).select_from(JobEmployee)) or 0,
        "job_tags": session.scalar(select(func.count()).select_from(JobTag)) or 0,
        "notes": session.scalar(select(func.count()).select_from(Note)) or 0,
        "invoices": session.scalar(select(func.count()).select_from(Invoice)) or 0,
        "invoice_items": session.scalar(select(func.count()).select_from(InvoiceItem)) or 0,
    }


def _referential_integrity_checks(session: Session) -> dict[str, bool]:
    missing_notes_jobs = session.scalar(
        select(func.count())
        .select_from(Note)
        .outerjoin(Job, Note.job_id == Job.id)
        .where(Job.id.is_(None))
    ) or 0

    missing_invoices_jobs = session.scalar(
        select(func.count())
        .select_from(Invoice)
        .outerjoin(Job, Invoice.job_id == Job.id)
        .where(Job.id.is_(None))
    ) or 0

    missing_items_invoice = session.scalar(
        select(func.count())
        .select_from(InvoiceItem)
        .outerjoin(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .where(Invoice.id.is_(None))
    ) or 0

    missing_job_employee_employee = session.scalar(
        select(func.count())
        .select_from(JobEmployee)
        .outerjoin(Employee, JobEmployee.employee_id == Employee.id)
        .where(Employee.id.is_(None))
    ) or 0

    missing_job_tag_job = session.scalar(
        select(func.count())
        .select_from(JobTag)
        .outerjoin(Job, JobTag.job_id == Job.id)
        .where(Job.id.is_(None))
    ) or 0

    missing_jobs_customers = session.scalar(
        select(func.count())
        .select_from(Job)
        .outerjoin(Customer, Job.customer_id == Customer.id)
        .where(Job.customer_id.is_not(None))
        .where(Customer.id.is_(None))
    ) or 0

    return {
        "fk_jobs_customers": missing_jobs_customers == 0,
        "fk_notes_jobs": missing_notes_jobs == 0,
        "fk_invoices_jobs": missing_invoices_jobs == 0,
        "fk_invoice_items_invoices": missing_items_invoice == 0,
        "fk_job_employees_employees": missing_job_employee_employee == 0,
        "fk_job_tags_jobs": missing_job_tag_job == 0,
    }


def run_ingestion(session: Session, source_data: dict[str, list[dict[str, str]]]) -> IngestionReport:
    issues = _source_reference_checks(source_data)

    city_candidates = {
        normalize_key(row.get("city", ""))
        for row in source_data["jobs"]
        if clean_text(row.get("city"))
    }

    customers_payload, addresses_payload = _parse_customers(source_data["customers"], city_candidates)
    employees_payload, employee_lookup = _parse_employees(source_data["employees"])
    jobs_payload, job_employees_payload, job_tags_payload, tech_issues = _parse_jobs_and_relations(
        source_data["jobs"],
        employee_lookup,
    )
    issues.extend(tech_issues)

    notes_payload = _parse_notes(source_data["notes"])
    invoices_payload = _parse_invoices(source_data["invoices"])
    invoice_items_payload, quantity_mode = _parse_invoice_items(source_data["invoice_items"])

    if issues:
        raise IngestionValidationError(issues)

    with session.begin():
        _upsert_rows(session, Customer, customers_payload)
        _upsert_rows(session, CustomerAddress, addresses_payload)
        _upsert_rows(session, Employee, employees_payload)
        _upsert_rows(session, Job, jobs_payload)
        _upsert_rows(session, JobEmployee, job_employees_payload)
        _upsert_rows(session, JobTag, job_tags_payload)
        _upsert_rows(session, Note, notes_payload)
        _upsert_rows(session, Invoice, invoices_payload)
        _upsert_rows(session, InvoiceItem, invoice_items_payload)

    db_counts = _fetch_db_counts(session)
    src_counts = source_counts(source_data)
    source_derived_counts = build_source_derived_counts(source_data, employee_lookup)
    parsed_counts = build_parsed_counts(
        customers_payload,
        addresses_payload,
        employees_payload,
        jobs_payload,
        job_employees_payload,
        job_tags_payload,
        notes_payload,
        invoices_payload,
        invoice_items_payload,
    )
    count_stages = build_count_stages(source_derived_counts, parsed_counts, db_counts)
    count_stage_checks = validate_count_stages(count_stages)

    source_amount_totals = {
        "jobs_total_amount": sum((row["total_amount"] or Decimal("0.00")) for row in jobs_payload),
        "invoices_amount": sum((row["amount"] or Decimal("0.00")) for row in invoices_payload),
        "invoice_items_amount": sum((row["amount"] or Decimal("0.00")) for row in invoice_items_payload),
        "invoice_items_quantity": sum((row["quantity"] or Decimal("0.00")) for row in invoice_items_payload),
    }

    db_amount_totals = {
        "jobs_total_amount": session.scalar(select(func.coalesce(func.sum(Job.total_amount), 0))) or Decimal("0.00"),
        "invoices_amount": session.scalar(select(func.coalesce(func.sum(Invoice.amount), 0))) or Decimal("0.00"),
        "invoice_items_amount": session.scalar(select(func.coalesce(func.sum(InvoiceItem.amount), 0))) or Decimal("0.00"),
        "invoice_items_quantity": session.scalar(select(func.coalesce(func.sum(InvoiceItem.quantity), 0))) or Decimal("0.00"),
    }

    fk_checks = _referential_integrity_checks(session)

    validation_checks = {
        "source_ids": db_counts["customers"] == source_derived_counts["customers"] and db_counts["jobs"] == source_derived_counts["jobs"] and db_counts["invoices"] == source_derived_counts["invoices"],
        "notes_are_canonical": db_counts["notes"] == source_derived_counts["notes"],
        "monetary_precision": source_amount_totals["jobs_total_amount"] == db_amount_totals["jobs_total_amount"] and source_amount_totals["invoices_amount"] == db_amount_totals["invoices_amount"] and source_amount_totals["invoice_items_amount"] == db_amount_totals["invoice_items_amount"],
        "quantity_semantics": source_amount_totals["invoice_items_quantity"] == db_amount_totals["invoice_items_quantity"],
        "duplicate_job_employee": len(job_employees_payload) == len({(r["job_id"], r["employee_id"]) for r in job_employees_payload}),
        "duplicate_job_tag": len(job_tags_payload) == len({(r["job_id"], r["tag"].casefold()) for r in job_tags_payload}),
        "duplicate_notes": len(notes_payload) == len({r["id"] for r in notes_payload}),
        "expected_counts": all(stage.matches for stage in count_stages.values()),
        "count_stage_consistency": all(count_stage_checks.values()),
        "technician_resolution": not any(issue.category in {"unresolved_technician", "ambiguous_technician"} for issue in issues),
        **fk_checks,
    }

    return IngestionReport(
        source_counts=src_counts,
        source_derived_counts=source_derived_counts,
        parsed_counts=parsed_counts,
        db_counts=db_counts,
        count_stages=count_stages,
        validation_checks=validation_checks,
        quantity_mode=quantity_mode,
        issues=[],
    )
