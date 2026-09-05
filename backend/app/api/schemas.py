from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentChatRequest(BaseModel):
    message: str
    session_id: int | None = None
    debug: bool = False


class AgentSessionCreateRequest(BaseModel):
    caller_identifier: str | None = None
    channel: str | None = None


class ToolCallDebug(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    status: str
    latency_ms: int
    error: dict[str, Any] | None = None


class AgentChatResponse(BaseModel):
    session_id: int
    response: str
    status: str
    needs_handoff: bool
    handoff_reason: str | None = None
    tool_calls: list[ToolCallDebug] | None = None
    active_customer_id: str | None = None
    active_job_id: str | None = None
    active_invoice_id: str | None = None
    referenced_note_ids: list[str] | None = None


class NoteReferenceSummary(BaseModel):
    position: int
    note_id: str
    job_id: str
    customer_id: str | None = None


class JobReferenceSummary(BaseModel):
    position: int
    job_id: str
    customer_id: str | None = None
    description: str | None = None


class AgentSessionSummary(BaseModel):
    context_version: int
    customer_id: str | None = None
    customer_display_name: str | None = None
    last_job_id: str | None = None
    last_invoice_id: str | None = None
    message_count: int = 0
    recent_note_references: list[NoteReferenceSummary] = Field(default_factory=list)
    recent_job_references: list[JobReferenceSummary] = Field(default_factory=list)


class AgentSessionResponse(BaseModel):
    session_id: int
    started_at: datetime
    ended_at: datetime | None = None
    channel: str
    status: str
    caller_identifier: str | None = None
    customer_id: str | None = None
    customer_display_name: str | None = None
    context_summary: AgentSessionSummary
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentSessionCloseResponse(BaseModel):
    session_id: int
    status: str


class AgentActionResponse(BaseModel):
    id: int
    timestamp: datetime
    action_type: str
    entity_type: str | None = None
    entity_id: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
