from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


ORDINAL_WORD_TO_INDEX = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
}


@dataclass(frozen=True)
class NoteReference:
    position: int
    note_id: str
    job_id: str
    customer_id: str | None
    content_preview: str
    similarity_score: float | None


@dataclass(frozen=True)
class JobReference:
    position: int
    job_id: str
    customer_id: str | None
    description: str | None


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str
    tool_call_id: str | None = None


@dataclass
class ConversationContext:
    context_version: int = 1
    customer_id: str | None = None
    customer_display_name: str | None = None
    last_job_id: str | None = None
    last_invoice_id: str | None = None
    recent_note_references: list[NoteReference] = field(default_factory=list)
    recent_job_references: list[JobReference] = field(default_factory=list)
    messages: list[ConversationMessage] = field(default_factory=list)

    @staticmethod
    def from_metadata(metadata: dict[str, Any] | None) -> ConversationContext:
        if not metadata:
            return ConversationContext()

        payload = metadata.get("conversation_context") if isinstance(metadata, dict) else None
        if not isinstance(payload, dict):
            return ConversationContext()

        note_refs = [
            NoteReference(
                position=int(item.get("position", 0)),
                note_id=str(item.get("note_id", "")),
                job_id=str(item.get("job_id", "")),
                customer_id=item.get("customer_id"),
                content_preview=str(item.get("content_preview", "")),
                similarity_score=float(item["similarity_score"]) if item.get("similarity_score") is not None else None,
            )
            for item in payload.get("recent_note_references", [])
            if isinstance(item, dict) and item.get("note_id") and item.get("job_id")
        ]
        job_refs = [
            JobReference(
                position=int(item.get("position", 0)),
                job_id=str(item.get("job_id", "")),
                customer_id=item.get("customer_id"),
                description=item.get("description"),
            )
            for item in payload.get("recent_job_references", [])
            if isinstance(item, dict) and item.get("job_id")
        ]
        messages = [
            ConversationMessage(
                role=str(item.get("role", "assistant")),
                content=str(item.get("content", "")),
                tool_call_id=item.get("tool_call_id"),
            )
            for item in payload.get("messages", [])
            if isinstance(item, dict) and item.get("role")
        ]

        return ConversationContext(
            context_version=int(payload.get("context_version", 1)),
            customer_id=payload.get("customer_id"),
            customer_display_name=payload.get("customer_display_name"),
            last_job_id=payload.get("last_job_id"),
            last_invoice_id=payload.get("last_invoice_id"),
            recent_note_references=note_refs,
            recent_job_references=job_refs,
            messages=messages,
        )

    def to_metadata_payload(self) -> dict[str, Any]:
        return {
            "context_version": self.context_version,
            "customer_id": self.customer_id,
            "customer_display_name": self.customer_display_name,
            "last_job_id": self.last_job_id,
            "last_invoice_id": self.last_invoice_id,
            "recent_note_references": [asdict(item) for item in self.recent_note_references],
            "recent_job_references": [asdict(item) for item in self.recent_job_references],
            "messages": [asdict(item) for item in self.messages],
        }

    def set_customer(self, customer_id: str | None, display_name: str | None) -> None:
        if customer_id and self.customer_id and customer_id != self.customer_id:
            self.clear_customer_scoped_references()
        self.customer_id = customer_id
        self.customer_display_name = display_name

    def clear_customer_scoped_references(self) -> None:
        self.last_job_id = None
        self.last_invoice_id = None
        self.recent_note_references = []
        self.recent_job_references = []

    def upsert_messages(self, new_messages: list[ConversationMessage], *, max_history_messages: int) -> None:
        self.messages.extend(new_messages)
        if len(self.messages) > max_history_messages:
            self.messages = self.messages[-max_history_messages:]

    def set_recent_note_references(self, rows: list[dict[str, Any]], *, max_refs: int = 10) -> None:
        refs: list[NoteReference] = []
        for index, row in enumerate(rows[:max_refs], start=1):
            content = str(row.get("content", ""))
            preview = re.sub(r"\s+", " ", content).strip()
            if len(preview) > 180:
                preview = preview[:177] + "..."
            refs.append(
                NoteReference(
                    position=index,
                    note_id=str(row.get("note_id", "")),
                    job_id=str(row.get("job_id", "")),
                    customer_id=row.get("customer_id"),
                    content_preview=preview,
                    similarity_score=float(row["similarity_score"]) if row.get("similarity_score") is not None else None,
                )
            )
        self.recent_note_references = refs
        if refs:
            self.last_job_id = refs[0].job_id
            if refs[0].customer_id:
                self.customer_id = refs[0].customer_id

    def set_recent_job_references(self, rows: list[dict[str, Any]], *, customer_id: str | None, max_refs: int = 10) -> None:
        refs: list[JobReference] = []
        for index, row in enumerate(rows[:max_refs], start=1):
            refs.append(
                JobReference(
                    position=index,
                    job_id=str(row.get("job_id", "")),
                    customer_id=customer_id,
                    description=row.get("description"),
                )
            )
        self.recent_job_references = refs
        if refs:
            self.last_job_id = refs[0].job_id

    def resolve_note_reference(self, user_text: str) -> NoteReference | None:
        lower = user_text.lower()

        number_match = re.search(r"\bnote\s*#?\s*(\d+)\b", lower)
        if number_match:
            wanted = int(number_match.group(1))
            return next((row for row in self.recent_note_references if row.position == wanted), None)

        for word, pos in ORDINAL_WORD_TO_INDEX.items():
            if re.search(rf"\b{word}\s+one\b", lower) or re.search(rf"\b{word}\b", lower):
                return next((row for row in self.recent_note_references if row.position == pos), None)

        if re.search(r"\bthat\s+note\b", lower) and self.recent_note_references:
            return self.recent_note_references[0]

        return None
