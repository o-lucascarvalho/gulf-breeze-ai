from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(val) for key, val in value.items()}
    return value


def compact_address(street: str | None, street_line_2: str | None, city: str | None, state: str | None, zip_code: str | None) -> str | None:
    pieces = [piece for piece in [street, street_line_2, city, state, zip_code] if piece]
    if not pieces:
        return None
    return ", ".join(pieces)
