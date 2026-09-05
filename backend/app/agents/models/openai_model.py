from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.agents.types import ModelResponse, ModelToolCall


class OpenAIToolCallingModel:
    def __init__(self, *, api_key: str, model_name: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model_name = model_name

    def run(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        formatted_tools = [self._format_function_tool(tool) for tool in tools]
        instructions, input_items = self._convert_messages(messages)

        request_kwargs: dict[str, Any] = {
            "model": self._model_name,
            "temperature": 0,
            "store": True,
            "input": input_items,
            "tools": formatted_tools,
            "tool_choice": "auto",
        }
        if instructions:
            request_kwargs["instructions"] = instructions

        response = self._client.responses.create(**request_kwargs)

        return self._parse_response(response)

    @staticmethod
    def _format_function_tool(tool: dict[str, Any]) -> dict[str, Any]:
        parameters = OpenAIToolCallingModel._normalize_function_tool_parameters(tool["input_schema"])

        return {
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "parameters": parameters,
            "strict": True,
        }

    @staticmethod
    def _normalize_function_tool_parameters(parameters: Any) -> Any:
        normalized = json.loads(json.dumps(parameters))

        def visit(node: Any) -> Any:
            if isinstance(node, dict):
                updated = {key: visit(value) for key, value in node.items()}
                if updated.get("type") == "object":
                    properties = updated.get("properties")
                    if isinstance(properties, dict):
                        updated["additionalProperties"] = False
                        updated["required"] = list(properties.keys())
                return updated

            if isinstance(node, list):
                return [visit(item) for item in node]

            return node

        return visit(normalized)

    @staticmethod
    def _convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        instructions: list[str] = []
        input_items: list[dict[str, Any]] = []

        for message in messages:
            role = message.get("role")
            content = message.get("content")

            if role in {"system", "developer"}:
                if content:
                    instructions.append(str(content))
                continue

            if role == "tool":
                call_id = message.get("tool_call_id") or message.get("call_id")
                if not call_id:
                    continue
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": OpenAIToolCallingModel._stringify_content(content),
                    }
                )
                continue

            if role == "assistant":
                content_text = OpenAIToolCallingModel._stringify_content(content)
                if content_text:
                    input_items.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": content_text,
                        }
                    )

                for tool_call in message.get("tool_calls") or []:
                    function_payload = tool_call.get("function") or {}
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": tool_call.get("id") or tool_call.get("call_id") or "",
                            "name": function_payload.get("name") or tool_call.get("name") or "",
                            "arguments": function_payload.get("arguments") or tool_call.get("arguments") or "{}",
                        }
                    )
                continue

            if role == "user":
                input_items.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": OpenAIToolCallingModel._stringify_content(content),
                    }
                )

        return "\n\n".join(item for item in instructions if item), input_items

    @staticmethod
    def _stringify_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        return json.dumps(content)

    @staticmethod
    def _parse_response(response: Any) -> ModelResponse:
        tool_calls: list[ModelToolCall] = []
        assistant_text_parts: list[str] = []

        for item in getattr(response, "output", []) or []:
            item_type = getattr(item, "type", None)
            if item_type == "function_call":
                raw_arguments = getattr(item, "arguments", "") or "{}"
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {}

                tool_calls.append(
                    ModelToolCall(
                        call_id=getattr(item, "call_id", ""),
                        name=getattr(item, "name", ""),
                        arguments=arguments,
                    )
                )
                continue

            if item_type == "message" and getattr(item, "role", None) == "assistant":
                for content_item in getattr(item, "content", []) or []:
                    if getattr(content_item, "type", None) == "output_text":
                        assistant_text_parts.append(getattr(content_item, "text", ""))

        message = "".join(assistant_text_parts).strip() or None
        if message is None:
            output_text = getattr(response, "output_text", "")
            if output_text:
                message = str(output_text).strip() or None

        return ModelResponse(message=message, tool_calls=tool_calls)
