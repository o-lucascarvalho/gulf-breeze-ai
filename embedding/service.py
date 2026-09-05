from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from embedding.exceptions import EmbeddingValidationError, TransientEmbeddingProviderError
from embedding.provider import EmbeddingProvider
from embedding.types import EmbeddingRunReport
from embedding.backend import ensure_backend_path

ensure_backend_path()

from app.db.models import Note  # noqa: E402


def validate_embedding_batch(vectors: list[list[float]], expected_count: int, expected_dimension: int) -> None:
    if len(vectors) != expected_count:
        raise EmbeddingValidationError(
            f"Embedding count mismatch: expected {expected_count}, got {len(vectors)}"
        )

    for index, vector in enumerate(vectors):
        if len(vector) != expected_dimension:
            raise EmbeddingValidationError(
                f"Embedding dimension mismatch at index {index}: expected {expected_dimension}, got {len(vector)}"
            )


def fetch_note_batch(
    session: Session,
    *,
    batch_size: int,
    force: bool,
    id_gt: str | None,
) -> list[Note]:
    stmt = select(Note).order_by(Note.id.asc()).limit(batch_size)

    if id_gt is not None:
        stmt = stmt.where(Note.id > id_gt)

    if not force:
        stmt = stmt.where(Note.embedding.is_(None))

    return list(session.scalars(stmt).all())


def run_embed_notes_pipeline(
    session_factory: Callable[[], Session],
    provider: EmbeddingProvider,
    *,
    expected_dimension: int,
    batch_size: int,
    max_retries: int,
    timeout: float,
    force: bool = False,
    limit: int | None = None,
) -> EmbeddingRunReport:
    started = time.time()

    with session_factory() as session:
        total_notes = session.scalar(select(func.count()).select_from(Note)) or 0
        already_embedded = session.scalar(
            select(func.count()).select_from(Note).where(Note.embedding.is_not(None))
        ) or 0

    if force:
        selected = total_notes if limit is None else min(total_notes, max(limit, 0))
        skipped = 0
    else:
        not_embedded = total_notes - already_embedded
        selected = not_embedded if limit is None else min(not_embedded, max(limit, 0))
        skipped = already_embedded

    processed = 0
    failed = 0
    api_batches = 0
    api_inputs = 0
    last_seen_id: str | None = None

    while processed < selected:
        remaining_quota = selected - processed
        current_batch_size = min(batch_size, remaining_quota)

        with session_factory() as batch_session:
            notes = fetch_note_batch(
                batch_session,
                batch_size=current_batch_size,
                force=force,
                id_gt=last_seen_id if force else None,
            )

            if not notes:
                break

            note_ids = [note.id for note in notes]
            texts = [note.content for note in notes]

        attempt = 0
        while True:
            try:
                vectors = provider.embed(texts, timeout=timeout)
                validate_embedding_batch(vectors, expected_count=len(texts), expected_dimension=expected_dimension)
                break
            except TransientEmbeddingProviderError:
                if attempt >= max_retries:
                    failed += len(texts)
                    elapsed = time.time() - started
                    with session_factory() as final_session:
                        remaining = final_session.scalar(
                            select(func.count()).select_from(Note).where(Note.embedding.is_(None))
                        ) or 0
                    return EmbeddingRunReport(
                        total_notes=total_notes,
                        already_embedded=already_embedded,
                        selected=selected,
                        processed=processed,
                        skipped=skipped,
                        failed=failed,
                        remaining=remaining,
                        api_batches=api_batches,
                        api_inputs=api_inputs,
                        elapsed_seconds=elapsed,
                    )
                sleep_seconds = 2**attempt
                time.sleep(sleep_seconds)
                attempt += 1
                continue
            except EmbeddingValidationError:
                failed += len(texts)
                elapsed = time.time() - started
                with session_factory() as final_session:
                    remaining = final_session.scalar(
                        select(func.count()).select_from(Note).where(Note.embedding.is_(None))
                    ) or 0
                return EmbeddingRunReport(
                    total_notes=total_notes,
                    already_embedded=already_embedded,
                    selected=selected,
                    processed=processed,
                    skipped=skipped,
                    failed=failed,
                    remaining=remaining,
                    api_batches=api_batches,
                    api_inputs=api_inputs,
                    elapsed_seconds=elapsed,
                )

        api_batches += 1
        api_inputs += len(texts)

        with session_factory() as write_session:
            with write_session.begin():
                persisted_notes = list(
                    write_session.scalars(select(Note).where(Note.id.in_(note_ids))).all()
                )
                mapping = {note.id: note for note in persisted_notes}
                for idx, note_id in enumerate(note_ids):
                    note = mapping[note_id]
                    if not force and note.embedding is not None:
                        continue
                    note.embedding = vectors[idx]

        processed += len(note_ids)
        if force:
            last_seen_id = note_ids[-1]

    with session_factory() as final_session:
        remaining = final_session.scalar(
            select(func.count()).select_from(Note).where(Note.embedding.is_(None))
        ) or 0

    elapsed = time.time() - started
    return EmbeddingRunReport(
        total_notes=total_notes,
        already_embedded=already_embedded,
        selected=selected,
        processed=processed,
        skipped=skipped,
        failed=failed,
        remaining=remaining,
        api_batches=api_batches,
        api_inputs=api_inputs,
        elapsed_seconds=elapsed,
    )
