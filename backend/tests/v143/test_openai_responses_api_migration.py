from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel

from app.agents.agent import AgentOrchestrator
from app.agents.models.openai_model import OpenAIToolCallingModel
from app.agents.types import ModelResponse, ModelToolCall
from app.tools.registry import ToolRegistry
from app.tools.types import ToolDefinition


class SearchCustomersInput(BaseModel):
    name: str


class BalanceInput(BaseModel):
    customer_id: str


class AppointmentsInput(BaseModel):
    customer_id: str | None = None


class SemanticInput(BaseModel):
    query: str
    customer_id: str | None = None


class GetNoteInput(BaseModel):
    note_id: str


@dataclass
class FakeResponsesEndpoint:
    scripted: list[Any]
    calls: list[dict[str, Any]]

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.scripted[len(self.calls) - 1]


class FakeOpenAIClient:
    def __init__(self, scripted: list[Any]) -> None:
        self.responses = FakeResponsesEndpoint(scripted=scripted, calls=[])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=MagicMock()))


@dataclass
class FakeModel:
    scripted: list[ModelResponse]
    calls: int = 0

    def run(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        response = self.scripted[self.calls]
        self.calls += 1
        return response


class InMemorySessionService:
    def __init__(self) -> None:
        self.next_id = 1
        self.records: dict[int, dict[str, Any]] = {}

    def create_session(self, *, channel: str, caller_identifier: str | None, metadata: dict | None) -> int:
        session_id = self.next_id
        self.next_id += 1
        self.records[session_id] = {
            "metadata_json": metadata or {},
            "status": "active",
            "customer_id": None,
        }
        return session_id

    def get_session(self, session_id: int):
        data = self.records.get(session_id)
        if not data:
            return None

        class Row:
            pass

        row = Row()
        row.metadata_json = data["metadata_json"]
        return row

    def update_session(
        self,
        *,
        session_id: int,
        status: str,
        customer_id: str | None,
        metadata_updates: dict | None = None,
        mark_ended: bool = True,
    ) -> None:
        row = self.records[session_id]
        row["status"] = status
        row["customer_id"] = customer_id
        if metadata_updates:
            merged = dict(row["metadata_json"])
            merged.update(metadata_updates)
            row["metadata_json"] = merged


class InMemoryActionService:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []

    def log_tool_call(self, **kwargs) -> None:
        self.actions.append(kwargs)


def make_response(*output_items: Any, output_text: str = "") -> Any:
    return SimpleNamespace(output=list(output_items), output_text=output_text)


def make_message(text: str) -> Any:
    return SimpleNamespace(
        type="message",
        role="assistant",
        content=[SimpleNamespace(type="output_text", text=text)],
        id="msg_1",
        status="completed",
    )


def make_function_call(call_id: str, name: str, arguments: str) -> Any:
    return SimpleNamespace(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=arguments,
        id=f"fc_{call_id}",
        status="completed",
    )


def make_reasoning_item() -> Any:
    return SimpleNamespace(type="reasoning", id="rs_1", status="completed")


def build_registry() -> ToolRegistry:
    def search(args: SearchCustomersInput) -> dict[str, Any]:
        if args.name == "Tamara Cruz":
            return {
                "matches": [{"customer_id": "cus_tamara", "first_name": "Tamara", "last_name": "Cruz"}],
                "ambiguous": False,
                "count": 1,
            }
        return {"matches": [], "ambiguous": False, "count": 0}

    def balance(args: BalanceInput) -> dict[str, Any]:
        return {"customer_id": args.customer_id, "balance": 9680.04, "open_count": 14, "currency": "USD"}

    def appointments(args: AppointmentsInput) -> dict[str, Any]:
        return {"customer_id": args.customer_id, "appointments": [{"status": "scheduled", "summary": "Pending schedule"}]}

    def semantic(args: SemanticInput) -> dict[str, Any]:
        return {
            "results": [
                {
                    "note_id": "nte_a",
                    "job_id": "job_a",
                    "customer_id": "cus_tamara",
                    "content": "System may be low on refrigerant.",
                    "similarity_score": 0.9,
                },
                {
                    "note_id": "nte_b",
                    "job_id": "job_b",
                    "customer_id": "cus_tamara",
                    "content": "Need to evacuate refrigerant before replacing reversing valve.",
                    "similarity_score": 0.8,
                },
            ],
            "count": 2,
        }

    def get_note(args: GetNoteInput) -> dict[str, Any]:
        if args.note_id == "nte_a":
            return {
                "note_id": "nte_a",
                "job_id": "job_a",
                "customer_id": "cus_tamara",
                "content": "System may be low on refrigerant.",
            }
        return {
            "note_id": "nte_b",
            "job_id": "job_b",
            "customer_id": "cus_tamara",
            "content": "Need to evacuate refrigerant before replacing reversing valve.",
        }

    return ToolRegistry(
        [
            ToolDefinition("search_customers", "", SearchCustomersInput, search),
            ToolDefinition("get_customer_balance", "", BalanceInput, balance),
            ToolDefinition("get_upcoming_appointments", "", AppointmentsInput, appointments),
            ToolDefinition("semantic_search_notes", "", SemanticInput, semantic),
            ToolDefinition("get_note", "", GetNoteInput, get_note),
        ]
    )


class OpenAIResponsesApiMigrationTests(unittest.TestCase):
    def test_responses_api_is_used_and_store_enabled(self) -> None:
        fake_client = FakeOpenAIClient([make_response(make_message("done"), output_text="done")])
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        result = model.run(
            messages=[
                {"role": "system", "content": "rules"},
                {"role": "developer", "content": "more rules"},
                {"role": "user", "content": "hi"},
            ],
            tools=[{"name": "tool", "description": "desc", "input_schema": {"type": "object"}}],
        )

        self.assertEqual(result.message, "done")
        self.assertEqual(len(fake_client.responses.calls), 1)
        self.assertFalse(fake_client.chat.completions.create.called)

        kwargs = fake_client.responses.calls[0]
        self.assertTrue(kwargs["store"])
        self.assertEqual(kwargs["model"], "gpt-test")
        self.assertEqual(kwargs["temperature"], 0)
        self.assertEqual(kwargs["instructions"], "rules\n\nmore rules")
        self.assertEqual(kwargs["input"], [{"type": "message", "role": "user", "content": "hi"}])
        self.assertEqual(kwargs["tools"][0]["name"], "tool")
        self.assertEqual(kwargs["tools"][0]["description"], "desc")
        self.assertEqual(kwargs["tools"][0]["parameters"], {"type": "object"})
        self.assertTrue(kwargs["tools"][0]["strict"])
        self.assertNotIn("previous_response_id", kwargs)

    def test_text_only_response_is_preserved(self) -> None:
        fake_client = FakeOpenAIClient([make_response(make_message("hello world"), output_text="hello world")])
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        result = model.run(messages=[{"role": "user", "content": "hi"}], tools=[])

        self.assertEqual(result.message, "hello world")
        self.assertEqual(result.tool_calls, [])

    def test_single_function_call_preserves_call_id_and_arguments(self) -> None:
        fake_client = FakeOpenAIClient(
            [make_response(make_function_call("call_123", "get_customer_balance", '{"customer_id":"cus_1"}'))]
        )
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        result = model.run(messages=[{"role": "user", "content": "balance"}], tools=[])

        self.assertIsNone(result.message)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].call_id, "call_123")
        self.assertEqual(result.tool_calls[0].name, "get_customer_balance")
        self.assertEqual(result.tool_calls[0].arguments, {"customer_id": "cus_1"})

    def test_multiple_function_calls_preserve_order(self) -> None:
        fake_client = FakeOpenAIClient(
            [
                make_response(
                    make_function_call("call_1", "search_customers", '{"name":"Tamara Cruz"}'),
                    make_function_call("call_2", "get_customer_balance", '{"customer_id":"cus_tamara"}'),
                )
            ]
        )
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        result = model.run(messages=[{"role": "user", "content": "test"}], tools=[])

        self.assertEqual([call.call_id for call in result.tool_calls], ["call_1", "call_2"])
        self.assertEqual([call.name for call in result.tool_calls], ["search_customers", "get_customer_balance"])

    def test_function_output_round_trip_uses_original_call_id(self) -> None:
        fake_client = FakeOpenAIClient([make_response(make_message("done"), output_text="done")])
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        model.run(
            messages=[
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call_abc", "function": {"name": "get_customer_balance", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "call_abc", "content": json.dumps({"ok": True, "data": {"balance": 9680.04}})},
            ],
            tools=[],
        )

        kwargs = fake_client.responses.calls[0]
        self.assertEqual(
            kwargs["input"],
            [
                {"type": "function_call", "call_id": "call_abc", "name": "get_customer_balance", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_abc", "output": '{"ok": true, "data": {"balance": 9680.04}}'},
            ],
        )

    def test_tool_schema_forwarding_is_preserved(self) -> None:
        fake_client = FakeOpenAIClient([make_response(make_message("done"), output_text="done")])
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        model.run(
            messages=[{"role": "user", "content": "hi"}],
            tools=[
                {
                    "name": "get_customer_balance",
                    "description": "Balance lookup",
                    "input_schema": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
                }
            ],
        )

        tool = fake_client.responses.calls[0]["tools"][0]
        self.assertEqual(tool["name"], "get_customer_balance")
        self.assertEqual(tool["description"], "Balance lookup")
        self.assertEqual(tool["parameters"]["required"], ["customer_id"])
        self.assertFalse(tool["parameters"]["additionalProperties"])
        self.assertTrue(tool["strict"])

    def test_invalid_function_arguments_do_not_crash_and_fall_back_to_empty_dict(self) -> None:
        fake_client = FakeOpenAIClient([make_response(make_function_call("call_bad", "get_customer_balance", "{not valid json"))])
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        result = model.run(messages=[{"role": "user", "content": "balance"}], tools=[])

        self.assertEqual(result.tool_calls[0].arguments, {})

    def test_mixed_output_returns_both_text_and_tool_calls(self) -> None:
        fake_client = FakeOpenAIClient(
            [
                make_response(
                    make_message("I need one tool."),
                    make_function_call("call_mixed", "search_customers", '{"name":"Tamara Cruz"}'),
                    output_text="I need one tool.",
                )
            ]
        )
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        result = model.run(messages=[{"role": "user", "content": "hi"}], tools=[])

        self.assertEqual(result.message, "I need one tool.")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].call_id, "call_mixed")

    def test_unknown_output_item_is_ignored(self) -> None:
        fake_client = FakeOpenAIClient(
            [make_response(make_reasoning_item(), make_message("final answer"), output_text="final answer")]
        )
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        result = model.run(messages=[{"role": "user", "content": "hi"}], tools=[])

        self.assertEqual(result.message, "final answer")
        self.assertEqual(result.tool_calls, [])

    def test_multi_round_agent_regression_uses_responses_api_and_tool_roundtrip(self) -> None:
        scripted = [
            make_response(make_function_call("call_1", "search_customers", '{"name":"Tamara Cruz"}')),
            make_response(make_function_call("call_2", "get_customer_balance", '{"customer_id":"cus_tamara"}')),
            make_response(make_message("Tamara Cruz currently owes $9,680.04."), output_text="Tamara Cruz currently owes $9,680.04."),
        ]
        fake_client = FakeOpenAIClient(scripted)
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        registry = build_registry()
        action_service = InMemoryActionService()
        agent = AgentOrchestrator(
            model=model,
            registry=registry,
            session_service=InMemorySessionService(),
            action_service=action_service,
            max_tool_rounds=8,
            max_history_messages=20,
        )

        result = agent.run("How much does Tamara Cruz currently owe?", finalize_session_on_return=False)

        self.assertEqual(result.response, "Tamara Cruz currently owes $9,680.04.")
        self.assertEqual([call["action_type"] for call in action_service.actions], ["search_customers", "get_customer_balance"])
        self.assertEqual(len(fake_client.responses.calls), 3)
        self.assertEqual(
            fake_client.responses.calls[1]["input"],
            [
                {"type": "message", "role": "user", "content": "How much does Tamara Cruz currently owe?"},
                {"type": "function_call", "call_id": "call_1", "name": "search_customers", "arguments": '{"name": "Tamara Cruz"}'},
                {"type": "function_call_output", "call_id": "call_1", "output": '{"ok": true, "data": {"matches": [{"customer_id": "cus_tamara", "first_name": "Tamara", "last_name": "Cruz"}], "ambiguous": false, "count": 1}}'},
            ],
        )
        self.assertEqual(fake_client.responses.calls[0]["store"], True)
        self.assertEqual(fake_client.responses.calls[1]["store"], True)
        self.assertEqual(fake_client.responses.calls[2]["store"], True)

    def test_deterministic_note_followup_regression_still_uses_get_note(self) -> None:
        scripted = [
            make_response(make_function_call("call_1", "search_customers", '{"name":"Tamara Cruz"}')),
            make_response(make_function_call("call_2", "semantic_search_notes", '{"query":"refrigerant leak","customer_id":"cus_tamara"}')),
            make_response(make_message("1. note one\n2. note two"), output_text="1. note one\n2. note two"),
            make_response(make_function_call("call_3", "semantic_search_notes", '{"query":"details about note 1"}')),
            make_response(make_message("Here are details for the requested note."), output_text="Here are details for the requested note."),
        ]
        fake_client = FakeOpenAIClient(scripted)
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]
        action_service = InMemoryActionService()

        agent = AgentOrchestrator(
            model=model,
            registry=build_registry(),
            session_service=InMemorySessionService(),
            action_service=action_service,
            max_tool_rounds=8,
            max_history_messages=20,
        )

        first = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)
        second = agent.run("Tell me more about note 1.", session_id=first.session_id, finalize_session_on_return=False)

        tool_actions = [action["action_type"] for action in action_service.actions]
        self.assertEqual(tool_actions, ["search_customers", "semantic_search_notes", "get_note"])
        self.assertEqual(second.referenced_note_ids[0], "nte_a")
        self.assertEqual(action_service.actions[2]["arguments"]["note_id"], "nte_a")

    def test_structured_balance_answer_stays_clean(self) -> None:
        scripted = [
            make_response(make_function_call("call_1", "search_customers", '{"name":"Tamara Cruz"}')),
            make_response(make_function_call("call_2", "get_customer_balance", '{"customer_id":"cus_tamara"}')),
            make_response(make_message("Tamara Cruz currently owes $9,680.04."), output_text="Tamara Cruz currently owes $9,680.04."),
        ]
        fake_client = FakeOpenAIClient(scripted)
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]
        agent = AgentOrchestrator(
            model=model,
            registry=build_registry(),
            session_service=InMemorySessionService(),
            action_service=InMemoryActionService(),
            max_tool_rounds=8,
            max_history_messages=20,
        )

        result = agent.run("How much does Tamara Cruz currently owe?", finalize_session_on_return=False)

        self.assertIn("$9,680.04", result.response)
        self.assertNotIn("available notes", result.response.lower())
        self.assertNotIn("causal relationship", result.response.lower())

    def test_no_hvac_interpretation_in_note_summary(self) -> None:
        scripted = [
            make_response(make_function_call("call_1", "search_customers", '{"name":"Tamara Cruz"}')),
            make_response(make_function_call("call_2", "semantic_search_notes", '{"query":"cooling issue","customer_id":"cus_tamara"}')),
            make_response(
                make_message("A technician recorded a liquid temperature of 180°F and noted that the water tower was not cooling the unit properly."),
                output_text="A technician recorded a liquid temperature of 180°F and noted that the water tower was not cooling the unit properly.",
            ),
        ]
        fake_client = FakeOpenAIClient(scripted)
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]
        agent = AgentOrchestrator(
            model=model,
            registry=build_registry(),
            session_service=InMemorySessionService(),
            action_service=InMemoryActionService(),
            max_tool_rounds=8,
            max_history_messages=20,
        )

        result = agent.run("What did the note say?", finalize_session_on_return=False)

        lower = result.response.lower()
        self.assertIn("liquid temperature", lower)
        self.assertIn("water tower", lower)
        self.assertNotIn("refrigerant issues", lower)
        self.assertNotIn("refrigerant leak", lower)

    def test_user_facing_ids_hidden_by_default(self) -> None:
        scripted = [
            make_response(make_function_call("call_1", "search_customers", '{"name":"Tamara Cruz"}')),
            make_response(make_function_call("call_2", "semantic_search_notes", '{"query":"refrigerant leak","customer_id":"cus_tamara"}')),
            make_response(make_message("The records mention refrigerant recovery and a possible leak search."), output_text="The records mention refrigerant recovery and a possible leak search."),
        ]
        fake_client = FakeOpenAIClient(scripted)
        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]
        agent = AgentOrchestrator(
            model=model,
            registry=build_registry(),
            session_service=InMemorySessionService(),
            action_service=InMemoryActionService(),
            max_tool_rounds=8,
            max_history_messages=20,
        )

        result = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)

        lower = result.response.lower()
        self.assertNotIn("note_id", lower)
        self.assertNotIn("job_id", lower)
        self.assertNotIn("customer_id", lower)


if __name__ == "__main__":
    unittest.main()
