from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.tools.types import ToolDefinition, ToolError, ToolExecutionRecord, ToolHandledError, validation_error_to_tool_error


@dataclass(frozen=True)
class ToolCallResult:
    payload: dict[str, Any]
    record: ToolExecutionRecord


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def list_tool_specs(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, tool_name: str, arguments: dict[str, Any] | None) -> ToolCallResult:
        started = time.perf_counter()
        args = arguments or {}

        tool = self._tools.get(tool_name)
        if tool is None:
            error = ToolError(code="unknown_tool", message=f"Unknown tool: {tool_name}")
            record = ToolExecutionRecord(
                name=tool_name,
                arguments=args,
                status="error",
                result=None,
                error=error,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            return ToolCallResult(payload={"ok": False, "error": error.__dict__}, record=record)

        try:
            parsed = tool.validate_arguments(args)
        except ValidationError as exc:
            error = validation_error_to_tool_error(exc)
            record = ToolExecutionRecord(
                name=tool_name,
                arguments=args,
                status="error",
                result=None,
                error=error,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            return ToolCallResult(payload={"ok": False, "error": error.__dict__}, record=record)

        try:
            data = tool.handler(parsed)
            status = "ok"
            result_payload = {"ok": True, "data": data}
            error = None
        except ToolHandledError as exc:
            status = "error"
            error = ToolError(code=exc.code, message=exc.message)
            data = None
            result_payload = {"ok": False, "error": error.__dict__}
        except Exception as exc:  # pragma: no cover
            status = "error"
            error = ToolError(code="tool_execution_failed", message=str(exc))
            data = None
            result_payload = {"ok": False, "error": error.__dict__}

        record = ToolExecutionRecord(
            name=tool.name,
            arguments=args,
            status=status,
            result=data,
            error=error,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return ToolCallResult(payload=result_payload, record=record)
