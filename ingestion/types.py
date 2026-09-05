from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationIssue:
    category: str
    message: str


@dataclass
class CountStage:
    source_label: str
    source: int
    parsed: int
    database: int

    @property
    def matches(self) -> bool:
        return self.source == self.parsed == self.database


@dataclass
class IngestionReport:
    source_counts: dict[str, int]
    source_derived_counts: dict[str, int]
    parsed_counts: dict[str, int]
    db_counts: dict[str, int]
    count_stages: dict[str, CountStage]
    validation_checks: dict[str, bool]
    quantity_mode: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(self.validation_checks.values()) and not self.issues
