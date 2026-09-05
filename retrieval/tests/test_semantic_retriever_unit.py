from __future__ import annotations

import unittest
from dataclasses import dataclass

from retrieval.exceptions import SemanticEmbeddingValidationError, SemanticQueryValidationError
from retrieval.service import MAX_TOP_K, PgVectorSemanticRetriever
from retrieval.types import SemanticSearchRow


@dataclass
class FakeEmbeddingProvider:
    vectors: list[list[float]]
    calls: int = 0
    last_texts: list[str] | None = None
    last_timeout: float | None = None

    model_name: str = "fake"

    def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        self.calls += 1
        self.last_texts = list(texts)
        self.last_timeout = timeout
        return [list(vector) for vector in self.vectors]


class FakeRepository:
    def __init__(self, rows: list[SemanticSearchRow]) -> None:
        self.rows = rows
        self.calls = 0
        self.last_query_embedding: list[float] | None = None
        self.last_top_k: int | None = None
        self.last_customer_id: str | None = None
        self.last_job_id: str | None = None

    def search_notes(
        self,
        session,
        *,
        query_embedding: list[float],
        top_k: int,
        customer_id: str | None = None,
        job_id: str | None = None,
    ) -> list[SemanticSearchRow]:
        self.calls += 1
        self.last_query_embedding = list(query_embedding)
        self.last_top_k = top_k
        self.last_customer_id = customer_id
        self.last_job_id = job_id
        return self.rows[:top_k]


class SemanticRetrieverUnitTests(unittest.TestCase):
    def _session_factory(self):
        class _NoopSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

        return _NoopSession()

    def test_query_embedded_once_and_vector_forwarded_to_repository(self) -> None:
        provider = FakeEmbeddingProvider(vectors=[[1.0, 0.0, 0.0]])
        repository = FakeRepository(
            [
                SemanticSearchRow(
                    note_id="n1",
                    job_id="job_1",
                    customer_id="cus_1",
                    content="content",
                    similarity_score=0.9,
                )
            ]
        )
        retriever = PgVectorSemanticRetriever(
            self._session_factory,
            provider,
            expected_dimension=3,
            timeout=12.5,
            repository=repository,
        )

        results = retriever.search("  previous refrigerant leak  ", top_k=1)

        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.last_texts, ["previous refrigerant leak"])
        self.assertEqual(provider.last_timeout, 12.5)
        self.assertEqual(repository.calls, 1)
        self.assertEqual(repository.last_query_embedding, [1.0, 0.0, 0.0])
        self.assertEqual(results[0].note_id, "n1")

    def test_invalid_query_rejected(self) -> None:
        provider = FakeEmbeddingProvider(vectors=[[1.0, 0.0, 0.0]])
        repository = FakeRepository([])
        retriever = PgVectorSemanticRetriever(
            self._session_factory,
            provider,
            expected_dimension=3,
            timeout=1.0,
            repository=repository,
        )

        with self.assertRaises(SemanticQueryValidationError):
            retriever.search("   ")

        self.assertEqual(provider.calls, 0)
        self.assertEqual(repository.calls, 0)

    def test_invalid_top_k_rejected(self) -> None:
        provider = FakeEmbeddingProvider(vectors=[[1.0, 0.0, 0.0]])
        repository = FakeRepository([])
        retriever = PgVectorSemanticRetriever(
            self._session_factory,
            provider,
            expected_dimension=3,
            timeout=1.0,
            repository=repository,
        )

        with self.assertRaises(SemanticQueryValidationError):
            retriever.search("query", top_k=0)

        with self.assertRaises(SemanticQueryValidationError):
            retriever.search("query", top_k=-1)

        with self.assertRaises(SemanticQueryValidationError):
            retriever.search("query", top_k=MAX_TOP_K + 1)

        self.assertEqual(provider.calls, 0)
        self.assertEqual(repository.calls, 0)

    def test_max_top_k_allowed(self) -> None:
        provider = FakeEmbeddingProvider(vectors=[[1.0, 0.0, 0.0]])
        repository = FakeRepository([])
        retriever = PgVectorSemanticRetriever(
            self._session_factory,
            provider,
            expected_dimension=3,
            timeout=1.0,
            repository=repository,
        )

        retriever.search("query", top_k=MAX_TOP_K)

        self.assertEqual(provider.calls, 1)
        self.assertEqual(repository.calls, 1)
        self.assertEqual(repository.last_top_k, MAX_TOP_K)

    def test_embedding_dimension_mismatch_rejected_before_database_search(self) -> None:
        provider = FakeEmbeddingProvider(vectors=[[1.0, 0.0]])
        repository = FakeRepository([])
        retriever = PgVectorSemanticRetriever(
            self._session_factory,
            provider,
            expected_dimension=3,
            timeout=1.0,
            repository=repository,
        )

        with self.assertRaises(SemanticEmbeddingValidationError):
            retriever.search("query")

        self.assertEqual(provider.calls, 1)
        self.assertEqual(repository.calls, 0)

    def test_query_embedding_count_mismatch_rejected_before_database_search(self) -> None:
        provider = FakeEmbeddingProvider(vectors=[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
        repository = FakeRepository([])
        retriever = PgVectorSemanticRetriever(
            self._session_factory,
            provider,
            expected_dimension=3,
            timeout=1.0,
            repository=repository,
        )

        with self.assertRaises(SemanticEmbeddingValidationError):
            retriever.search("query")

        self.assertEqual(provider.calls, 1)
        self.assertEqual(repository.calls, 0)


if __name__ == "__main__":
    unittest.main()
