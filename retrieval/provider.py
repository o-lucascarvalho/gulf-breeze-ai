from __future__ import annotations

from typing import Protocol

from retrieval.types import SemanticSearchResult


class StructuredRetrieval(Protocol):
    """Reserved concept for SQL retrieval in a future version."""


class SemanticRetrieval(Protocol):
    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        customer_id: str | None = None,
        job_id: str | None = None,
    ) -> list[SemanticSearchResult]:
        """Return ranked semantic matches from notes."""


SemanticRetriever = SemanticRetrieval
