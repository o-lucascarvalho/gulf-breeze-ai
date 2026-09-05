from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    message: str | None
    tool_calls: list[ModelToolCall] = field(default_factory=list)


class AgentModel(Protocol):
    def run(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        """Return either assistant text, tool calls, or both."""


@dataclass(frozen=True)
class AgentToolCall:
    tool_name: str
    arguments: dict[str, Any]
    status: str
    latency_ms: int
    error: dict[str, Any] | None


@dataclass(frozen=True)
class AgentResult:
    response: str
    session_id: int
    status: str
    needs_handoff: bool
    handoff_reason: str | None
    tool_calls: list[AgentToolCall]
    active_customer_id: str | None = None
    active_job_id: str | None = None
    active_invoice_id: str | None = None
    referenced_note_ids: list[str] = field(default_factory=list)
