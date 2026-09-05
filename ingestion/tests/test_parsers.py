from __future__ import annotations

import unittest
from decimal import Decimal

from ingestion.parsers import (
    detect_quantity_mode,
    parse_money,
    parse_quantity,
    split_pipe_addresses,
    split_semicolon_list,
)


class ParserTests(unittest.TestCase):
    def test_parse_pipe_separated_addresses(self) -> None:
        raw = "101 Main St Miami FL 33101 | 202 Main St Miami FL 33101 | 202 Main St Miami FL 33101"
        parts = split_pipe_addresses(raw)
        self.assertEqual(
            parts,
            [
                "101 Main St Miami FL 33101",
                "202 Main St Miami FL 33101",
                "202 Main St Miami FL 33101",
            ],
        )

    def test_parse_job_technicians(self) -> None:
        raw = "Audrey Farrell; Yvonne Aguilar; Theo Graves"
        self.assertEqual(split_semicolon_list(raw), ["Audrey Farrell", "Yvonne Aguilar", "Theo Graves"])

    def test_parse_tags(self) -> None:
        raw = "Outside Sales - Vivian; Registration Complete"
        self.assertEqual(split_semicolon_list(raw), ["Outside Sales - Vivian", "Registration Complete"])

    def test_invoice_item_quantity_conversion_hundredths(self) -> None:
        mode = detect_quantity_mode(["100", "250"])
        self.assertEqual(mode, "hundredths")
        self.assertEqual(parse_quantity("100", mode), Decimal("1.00"))
        self.assertEqual(parse_quantity("250", mode), Decimal("2.50"))

    def test_invoice_item_quantity_conversion_decimal(self) -> None:
        mode = detect_quantity_mode(["1.0", "2.5"])
        self.assertEqual(mode, "decimal")
        self.assertEqual(parse_quantity("1.0", mode), Decimal("1.00"))
        self.assertEqual(parse_quantity("2.5", mode), Decimal("2.50"))

    def test_monetary_precision(self) -> None:
        self.assertEqual(parse_money("18174.9"), Decimal("18174.90"))
        self.assertEqual(parse_money("0.0"), Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
