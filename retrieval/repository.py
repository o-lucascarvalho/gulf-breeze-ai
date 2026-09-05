from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from retrieval.backend import ensure_backend_path
from retrieval.types import SemanticSearchRow

ensure_backend_path()

from app.db.models import Job, Note  # noqa: E402


class NotesSemanticRepository:
    def search_notes(
        self,
        session: Session,
        *,
        query_embedding: list[float],
        top_k: int,
        customer_id: str | None = None,
        job_id: str | None = None,
    ) -> list[SemanticSearchRow]:
        distance_expr = Note.embedding.cosine_distance(query_embedding)
        similarity_expr = (1 - distance_expr).label("similarity_score")

        stmt = (
            select(
                Note.id.label("note_id"),
                Note.job_id.label("job_id"),
                Job.customer_id.label("customer_id"),
                Note.content.label("content"),
                similarity_expr,
            )
            .join(Job, Job.id == Note.job_id)
            .where(Note.embedding.is_not(None))
        )

        if customer_id is not None:
            stmt = stmt.where(Job.customer_id == customer_id)

        if job_id is not None:
            stmt = stmt.where(Note.job_id == job_id)

        stmt = stmt.order_by(similarity_expr.desc(), Note.id.asc()).limit(top_k)

        rows = session.execute(stmt).all()
        return [
            SemanticSearchRow(
                note_id=row.note_id,
                job_id=row.job_id,
                customer_id=row.customer_id,
                content=row.content,
                similarity_score=float(row.similarity_score),
            )
            for row in rows
        ]
