from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal("0.01")


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def parse_int(value: str | None) -> int | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    return int(cleaned)


def parse_iso_datetime(value: str | None) -> datetime | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def parse_iso_date(value: str | None) -> date | None:
    dt = parse_iso_datetime(value)
    if dt is None:
        return None
    return dt.date()


def parse_money(value: str | None) -> Decimal | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    return Decimal(cleaned).quantize(CENTS, rounding=ROUND_HALF_UP)


def detect_quantity_mode(quantity_values: list[str]) -> str:
    has_decimal = any("." in (v or "") for v in quantity_values if (v or "").strip())
    return "decimal" if has_decimal else "hundredths"


def parse_quantity(value: str | None, mode: str) -> Decimal | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    if mode == "hundredths":
        return (Decimal(cleaned) / Decimal("100")).quantize(CENTS, rounding=ROUND_HALF_UP)
    return Decimal(cleaned).quantize(CENTS, rounding=ROUND_HALF_UP)


def split_pipe_addresses(value: str | None) -> list[str]:
    cleaned = clean_text(value)
    if cleaned is None:
        return []
    return [part.strip() for part in cleaned.split("|") if part.strip()]


def split_semicolon_list(value: str | None) -> list[str]:
    cleaned = clean_text(value)
    if cleaned is None:
        return []
    return [part.strip() for part in cleaned.split(";") if part.strip()]


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_key(value: str) -> str:
    return normalize_spaces(value).casefold()


def parse_address_components(address: str, city_candidates: set[str]) -> dict[str, str | None]:
    normalized = normalize_spaces(address)
    city = None
    state = None
    zip_code = None
    street = normalized

    match = re.search(r"\s([A-Z]{2})\s(\d{5}(?:-\d{4})?)$", normalized)
    body = normalized
    if match:
        state = match.group(1)
        zip_code = match.group(2)
        body = normalized[: match.start()].strip()

    best_city = ""
    body_lower = body.casefold()
    for candidate in city_candidates:
        if body_lower.endswith(" " + candidate) or body_lower == candidate:
            if len(candidate) > len(best_city):
                best_city = candidate

    if best_city:
        city = best_city.title()
        street = body[: -len(best_city)].strip()
        if street.endswith(","):
            street = street[:-1].strip()
    else:
        street = body

    return {
        "street": street or None,
        "street_line_2": None,
        "city": city,
        "state": state,
        "zip": zip_code,
        "normalized_address": normalized,
    }


def build_customer_address_id(customer_id: str, normalized_address: str) -> str:
    digest = hashlib.sha1(normalized_address.encode("utf-8")).hexdigest()[:16]
    return f"{customer_id}:{digest}"


def build_invoice_item_id(invoice_id: str, row_index: int) -> str:
    return f"{invoice_id}:item:{row_index}"
