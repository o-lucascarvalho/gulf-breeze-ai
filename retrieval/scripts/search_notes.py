from __future__ import annotations

import argparse
import sys

from embedding.openai_provider import OpenAIEmbeddingProvider
from retrieval.backend import ensure_backend_path
from retrieval.exceptions import SemanticRetrievalError
from retrieval.service import PgVectorSemanticRetriever

ensure_backend_path()

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.db.vector_settings import NOTE_EMBEDDING_DIMENSION  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Semantic search over notes embeddings")
    parser.add_argument("query", help="Natural language query text")
    parser.add_argument("--top-k", type=int, default=5, help="Maximum number of results")
    parser.add_argument("--customer-id", type=str, default=None, help="Optional customer filter")
    parser.add_argument("--job-id", type=str, default=None, help="Optional job filter")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()

    if not settings.openai_api_key:
        print("OPENAI_API_KEY is required.", file=sys.stderr)
        return 1

    if settings.embedding_dimension != NOTE_EMBEDDING_DIMENSION:
        print(
            "Configured EMBEDDING_DIMENSION does not match notes.embedding schema dimension.",
            file=sys.stderr,
        )
        return 1

    provider = OpenAIEmbeddingProvider(
        api_key=settings.openai_api_key,
        model_name=settings.openai_embedding_model,
    )

    retriever = PgVectorSemanticRetriever(
        SessionLocal,
        provider,
        expected_dimension=settings.embedding_dimension,
        timeout=settings.embedding_timeout,
        max_top_k=settings.semantic_max_top_k,
    )

    try:
        results = retriever.search(
            args.query,
            top_k=args.top_k,
            customer_id=args.customer_id,
            job_id=args.job_id,
        )
    except SemanticRetrievalError as exc:
        print(f"Semantic retrieval failed: {exc}", file=sys.stderr)
        return 2

    print("Gulf Breeze Air - Semantic Retrieval")
    print("")
    print(f"query:       {args.query}")
    print(f"top_k:       {args.top_k}")
    print(f"customer_id: {args.customer_id or '-'}")
    print(f"job_id:      {args.job_id or '-'}")
    print("")

    if not results:
        print("No results.")
        return 0

    for idx, row in enumerate(results, start=1):
        preview = row.content.replace("\n", " ").strip()
        if len(preview) > 120:
            preview = preview[:117] + "..."
        print(
            f"{idx:02d}. note_id={row.note_id} job_id={row.job_id} "
            f"customer_id={row.customer_id or '-'} score={row.similarity_score:.6f}"
        )
        print(f"    {preview}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
