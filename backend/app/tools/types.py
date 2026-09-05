from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


class ToolHandledError(RuntimeError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str


@dataclass(frozen=True)
class ToolExecutionRecord:
    name: str
    arguments: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    error: ToolError | None
    latency_ms: int


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], dict[str, Any]]

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }

    def validate_arguments(self, arguments: dict[str, Any]) -> BaseModel:
        return self.input_model.model_validate(arguments)


def validation_error_to_tool_error(exc: ValidationError) -> ToolError:
    details = "; ".join(err.get("msg", "invalid value") for err in exc.errors())
    return ToolError(code="invalid_tool_arguments", message=details or "Invalid tool arguments")
