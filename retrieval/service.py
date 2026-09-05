from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from embedding.provider import EmbeddingProvider
from retrieval.exceptions import SemanticEmbeddingValidationError, SemanticQueryValidationError
from retrieval.repository import NotesSemanticRepository
from retrieval.types import SemanticSearchResult


MAX_TOP_K = 50


class PgVectorSemanticRetriever:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        embedding_provider: EmbeddingProvider,
        *,
        expected_dimension: int,
        timeout: float,
        max_top_k: int = MAX_TOP_K,
        repository: NotesSemanticRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._embedding_provider = embedding_provider
        self._expected_dimension = expected_dimension
        self._timeout = timeout
        self._max_top_k = max_top_k
        self._repository = repository or NotesSemanticRepository()

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        customer_id: str | None = None,
        job_id: str | None = None,
    ) -> list[SemanticSearchResult]:
        normalized_query = query.strip()
        if not normalized_query:
            raise SemanticQueryValidationError("Query must be a non-empty string.")

        if top_k < 1:
            raise SemanticQueryValidationError("top_k must be >= 1.")

        if top_k > self._max_top_k:
            raise SemanticQueryValidationError(f"top_k must be <= {self._max_top_k}.")

        query_embeddings = self._embedding_provider.embed([normalized_query], timeout=self._timeout)
        if len(query_embeddings) != 1:
            raise SemanticEmbeddingValidationError(
                f"Query embedding count mismatch: expected 1, got {len(query_embeddings)}"
            )

        query_embedding = query_embeddings[0]
        if len(query_embedding) != self._expected_dimension:
            raise SemanticEmbeddingValidationError(
                "Query embedding dimension mismatch: "
                f"expected {self._expected_dimension}, got {len(query_embedding)}"
            )

        with self._session_factory() as session:
            rows = self._repository.search_notes(
                session,
                query_embedding=query_embedding,
                top_k=top_k,
                customer_id=customer_id,
                job_id=job_id,
            )

        return [
            SemanticSearchResult(
                note_id=row.note_id,
                job_id=row.job_id,
                customer_id=row.customer_id,
                content=row.content,
                similarity_score=row.similarity_score,
            )
            for row in rows
        ]
