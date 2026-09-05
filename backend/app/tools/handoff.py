from __future__ import annotations

from pydantic import BaseModel, Field

from app.tools.types import ToolDefinition


class RequestHandoffInput(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def build_handoff_tool() -> ToolDefinition:
    def request_handoff(args: RequestHandoffInput) -> dict:
        return {
            "needs_handoff": True,
            "handoff_reason": args.reason.strip(),
        }

    return ToolDefinition(
        name="request_handoff",
        description=(
            "Request human office handoff when the user asks for unsupported actions, records are ambiguous, or data "
            "cannot be resolved safely."
        ),
        input_model=RequestHandoffInput,
        handler=request_handoff,
    )
