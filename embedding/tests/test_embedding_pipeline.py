from __future__ import annotations

import unittest
from dataclasses import dataclass

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from embedding.backend import ensure_backend_path
from embedding.exceptions import TransientEmbeddingProviderError
from embedding.service import run_embed_notes_pipeline, validate_embedding_batch

ensure_backend_path()

from app.db.base import Base  # noqa: E402
from app.db.models import Customer, Job, Note  # noqa: E402


@dataclass
class FakeProvider:
    vectors: list[list[float]]
    calls: int = 0

    model_name: str = "fake"

    def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        self.calls += 1
        return [self.vectors[i] for i in range(len(texts))]


class TransientThenSuccessProvider:
    def __init__(self, vectors: list[list[float]], fail_calls: int = 1) -> None:
        self.model_name = "fake"
        self.calls = 0
        self._vectors = vectors
        self._fail_calls = fail_calls

    def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        self.calls += 1
        if self.calls <= self._fail_calls:
            raise TransientEmbeddingProviderError("temporary")
        return [self._vectors[i] for i in range(len(texts))]


class CountMismatchProvider:
    model_name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        self.calls += 1
        return [[0.1, 0.2, 0.3]]


class EmbeddingPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine, tables=[Customer.__table__, Job.__table__, Note.__table__])
        self.Session = sessionmaker(bind=self.engine)

        with self.Session() as session:
            session.add(Customer(id="cus_1"))
            session.add(Job(id="job_1", customer_id="cus_1", description="A"))
            session.add(Job(id="job_2", customer_id="cus_1", description="B"))
            session.add(Job(id="job_3", customer_id="cus_1", description="C"))
            session.add(Note(id="n1", job_id="job_1", note_index=1, content="note one"))
            session.add(Note(id="n2", job_id="job_2", note_index=1, content="note two"))
            session.add(Note(id="n3", job_id="job_3", note_index=1, content="note three"))
            session.commit()

    def test_provider_contract_one_input_one_embedding(self) -> None:
        validate_embedding_batch([[0.1, 0.2, 0.3]], expected_count=1, expected_dimension=3)

    def test_provider_contract_multiple_inputs_preserve_order(self) -> None:
        provider = FakeProvider(vectors=[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
        report = run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=10,
            max_retries=0,
            timeout=1.0,
            limit=2,
        )
        self.assertEqual(report.processed, 2)
        with self.Session() as session:
            n1 = session.get(Note, "n1")
            n2 = session.get(Note, "n2")
            self.assertEqual(list(n1.embedding), [1.0, 0.0, 0.0])
            self.assertEqual(list(n2.embedding), [2.0, 0.0, 0.0])

    def test_provider_contract_incorrect_number_of_embeddings_fails(self) -> None:
        provider = CountMismatchProvider()
        report = run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=2,
            max_retries=0,
            timeout=1.0,
            limit=2,
        )
        self.assertEqual(report.failed, 2)
        self.assertEqual(provider.calls, 1)

    def test_provider_contract_incorrect_dimension_fails(self) -> None:
        provider = FakeProvider(vectors=[[0.1, 0.2], [0.3, 0.4]])
        report = run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=2,
            max_retries=0,
            timeout=1.0,
            limit=2,
        )
        self.assertEqual(report.failed, 2)

    def test_only_null_embeddings_selected_by_default(self) -> None:
        with self.Session() as session:
            note = session.get(Note, "n1")
            note.embedding = [9.0, 9.0, 9.0]
            session.commit()

        provider = FakeProvider(vectors=[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
        report = run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=10,
            max_retries=0,
            timeout=1.0,
        )

        self.assertEqual(report.selected, 2)
        self.assertEqual(report.skipped, 1)
        with self.Session() as session:
            self.assertEqual(list(session.get(Note, "n1").embedding), [9.0, 9.0, 9.0])

    def test_force_reprocesses_existing_embeddings(self) -> None:
        with self.Session() as session:
            for note_id in ["n1", "n2", "n3"]:
                session.get(Note, note_id).embedding = [8.0, 8.0, 8.0]
            session.commit()

        provider = FakeProvider(vectors=[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0]])
        report = run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=2,
            max_retries=0,
            timeout=1.0,
            force=True,
        )

        self.assertEqual(report.selected, 3)
        self.assertEqual(report.processed, 3)
        with self.Session() as session:
            self.assertEqual(list(session.get(Note, "n1").embedding), [1.0, 0.0, 0.0])
            self.assertEqual(list(session.get(Note, "n3").embedding), [1.0, 0.0, 0.0])

    def test_batch_processing_and_isolated_commits(self) -> None:
        class FailSecondBatchProvider:
            model_name = "fake"

            def __init__(self) -> None:
                self.calls = 0

            def embed(self, texts: list[str], timeout: float) -> list[list[float]]:
                self.calls += 1
                if self.calls == 2:
                    return [[0.1, 0.2]] * len(texts)
                return [[0.1, 0.2, 0.3]] * len(texts)

        provider = FailSecondBatchProvider()
        report = run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=2,
            max_retries=0,
            timeout=1.0,
            force=True,
        )

        self.assertEqual(report.processed, 2)
        self.assertEqual(report.failed, 1)
        with self.Session() as session:
            self.assertIsNotNone(session.get(Note, "n1").embedding)
            self.assertIsNotNone(session.get(Note, "n2").embedding)
            self.assertIsNone(session.get(Note, "n3").embedding)

    def test_retry_behavior_for_transient_failure(self) -> None:
        provider = TransientThenSuccessProvider(vectors=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], fail_calls=1)
        report = run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=2,
            max_retries=2,
            timeout=1.0,
            limit=2,
        )
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.processed, 2)
        self.assertEqual(provider.calls, 2)

    def test_non_retryable_validation_failure_is_not_retried(self) -> None:
        provider = CountMismatchProvider()
        report = run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=2,
            max_retries=5,
            timeout=1.0,
            limit=2,
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(report.failed, 2)

    def test_pipeline_idempotency(self) -> None:
        provider_first = FakeProvider(vectors=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]])
        first = run_embed_notes_pipeline(
            self.Session,
            provider_first,
            expected_dimension=3,
            batch_size=2,
            max_retries=0,
            timeout=1.0,
        )
        self.assertEqual(first.processed, 3)

        provider_second = FakeProvider(vectors=[[9.0, 9.0, 9.0]])
        second = run_embed_notes_pipeline(
            self.Session,
            provider_second,
            expected_dimension=3,
            batch_size=2,
            max_retries=0,
            timeout=1.0,
        )
        self.assertEqual(second.processed, 0)
        self.assertEqual(second.selected, 0)
        self.assertEqual(second.api_inputs, 0)

    def test_embedding_updates_do_not_modify_note_integrity_fields(self) -> None:
        with self.Session() as session:
            before = {
                row.id: (row.content, row.job_id, row.note_index)
                for row in session.query(Note).order_by(Note.id)
            }

        provider = FakeProvider(vectors=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]])
        run_embed_notes_pipeline(
            self.Session,
            provider,
            expected_dimension=3,
            batch_size=2,
            max_retries=0,
            timeout=1.0,
        )

        with self.Session() as session:
            after = {
                row.id: (row.content, row.job_id, row.note_index)
                for row in session.query(Note).order_by(Note.id)
            }

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
