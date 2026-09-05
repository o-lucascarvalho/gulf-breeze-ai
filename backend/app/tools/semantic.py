from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel, Field

from app.tools.helpers import to_jsonable
from app.tools.types import ToolDefinition

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from retrieval.provider import SemanticRetriever  # noqa: E402


class SemanticSearchNotesInput(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    customer_id: str | None = None
    job_id: str | None = None


def build_semantic_tool(semantic_retriever: SemanticRetriever) -> ToolDefinition:
    def semantic_search_notes(args: SemanticSearchNotesInput) -> dict:
        results = semantic_retriever.search(
            args.query,
            top_k=args.top_k,
            customer_id=args.customer_id,
            job_id=args.job_id,
        )
        return {
            "results": to_jsonable(
                [
                    {
                        "note_id": row.note_id,
                        "job_id": row.job_id,
                        "customer_id": row.customer_id,
                        "content": row.content,
                        "similarity_score": row.similarity_score,
                    }
                    for row in results
                ]
            ),
            "count": len(results),
        }

    return ToolDefinition(
        name="semantic_search_notes",
        description=(
            "Search technician and operational notes by semantic meaning. Use this for prior symptoms, diagnoses, "
            "repairs, complaints, and recurring issues in free-text notes. Do not use for balances, schedules, or "
            "identity resolution."
        ),
        input_model=SemanticSearchNotesInput,
        handler=semantic_search_notes,
    )
