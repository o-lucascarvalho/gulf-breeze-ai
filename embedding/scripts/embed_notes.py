from __future__ import annotations

import argparse
import sys

from embedding.backend import ensure_backend_path
from embedding.exceptions import EmbeddingValidationError
from embedding.openai_provider import OpenAIEmbeddingProvider
from embedding.service import run_embed_notes_pipeline

ensure_backend_path()

from app.core.config import get_settings  # noqa: E402
from app.db.vector_settings import NOTE_EMBEDDING_DIMENSION  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and persist note embeddings")
    parser.add_argument("--batch-size", type=int, default=None, help="Batch size for embedding generation")
    parser.add_argument("--force", action="store_true", help="Re-embed notes even when embeddings already exist")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of notes selected for this run")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()

    if not settings.openai_api_key:
        print("OPENAI_API_KEY is required.", file=sys.stderr)
        return 1

    provider = OpenAIEmbeddingProvider(
        api_key=settings.openai_api_key,
        model_name=settings.openai_embedding_model,
    )

    batch_size = args.batch_size or settings.embedding_batch_size
    if batch_size <= 0:
        print("Batch size must be greater than 0.", file=sys.stderr)
        return 1

    if settings.embedding_dimension != NOTE_EMBEDDING_DIMENSION:
        print(
            "Configured EMBEDDING_DIMENSION does not match notes.embedding schema dimension.",
            file=sys.stderr,
        )
        return 1

    try:
        report = run_embed_notes_pipeline(
            SessionLocal,
            provider,
            expected_dimension=settings.embedding_dimension,
            batch_size=batch_size,
            max_retries=settings.embedding_max_retries,
            timeout=settings.embedding_timeout,
            force=args.force,
            limit=args.limit,
        )
    except EmbeddingValidationError as exc:
        print(f"Embedding validation failed: {exc}", file=sys.stderr)
        return 2

    print("Gulf Breeze Air - Note Embedding")
    print("")
    print(f"model:             {settings.openai_embedding_model}")
    print(f"dimension:         {settings.embedding_dimension}")
    print(f"batch_size:        {batch_size}")
    print("")
    print(f"total notes:       {report.total_notes}")
    print(f"already embedded:  {report.already_embedded}")
    print(f"selected:          {report.selected}")
    print(f"processed:         {report.processed}")
    print(f"skipped:           {report.skipped}")
    print(f"failed:            {report.failed}")
    print(f"remaining:         {report.remaining}")
    print("")
    print(f"api batches:       {report.api_batches}")
    print(f"api inputs:        {report.api_inputs}")
    print(f"elapsed_sec:       {report.elapsed_seconds:.2f}")

    if report.failed > 0:
        print("Embedding completed with failures.")
        return 3

    print("Embedding completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
