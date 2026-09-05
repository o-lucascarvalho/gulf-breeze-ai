from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, Note


@dataclass(frozen=True)
class NoteDetail:
    note_id: str
    job_id: str
    customer_id: str | None
    content: str


class NoteRepository:
    def get_note(self, session: Session, note_id: str) -> NoteDetail | None:
        stmt = (
            select(
                Note.id.label("note_id"),
                Note.job_id.label("job_id"),
                Job.customer_id.label("customer_id"),
                Note.content.label("content"),
            )
            .join(Job, Job.id == Note.job_id)
            .where(Note.id == note_id)
            .limit(1)
        )
        row = session.execute(stmt).first()
        if row is None:
            return None
        return NoteDetail(
            note_id=row.note_id,
            job_id=row.job_id,
            customer_id=row.customer_id,
            content=row.content,
        )
