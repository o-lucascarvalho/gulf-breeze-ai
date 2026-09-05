from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticSearchResult:
    note_id: str
    job_id: str
    customer_id: str | None
    content: str
    similarity_score: float


@dataclass(frozen=True)
class SemanticSearchRow:
    note_id: str
    job_id: str
    customer_id: str | None
    content: str
    similarity_score: float
