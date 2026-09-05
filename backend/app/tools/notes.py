from __future__ import annotations

from pydantic import BaseModel

from app.repositories.note_repository import NoteRepository
from app.tools.helpers import to_jsonable
from app.tools.types import ToolDefinition, ToolHandledError


class GetNoteInput(BaseModel):
    note_id: str


def build_note_tools(session_factory) -> list[ToolDefinition]:
    repository = NoteRepository()

    def get_note(args: GetNoteInput) -> dict:
        with session_factory() as session:
            row = repository.get_note(session, args.note_id)

        if row is None:
            raise ToolHandledError(
                code="note_not_found",
                message="No note was found for the requested note ID.",
            )

        return to_jsonable(
            {
                "note_id": row.note_id,
                "job_id": row.job_id,
                "customer_id": row.customer_id,
                "content": row.content,
            }
        )

    return [
        ToolDefinition(
            name="get_note",
            description=(
                "Retrieve one exact service note by note ID. Use this when the conversation already references a "
                "specific note, such as note 1 or that note. Do not use semantic search when exact note_id is known."
            ),
            input_model=GetNoteInput,
            handler=get_note,
        )
    ]
