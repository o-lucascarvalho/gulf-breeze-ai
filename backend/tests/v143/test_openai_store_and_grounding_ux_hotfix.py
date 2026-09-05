from __future__ import annotations

import unittest
from dataclasses import dataclass
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
        self.records[session_id] = {"metadata_json": metadata or {}, "status": "active", "customer_id": None}
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

    def update_session(self, *, session_id: int, status: str, customer_id: str | None, metadata_updates: dict | None = None, mark_ended: bool = True) -> None:
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


def build_registry(note_texts: list[str]) -> ToolRegistry:
    def search(args: SearchCustomersInput) -> dict[str, Any]:
        return {"matches": [{"customer_id": "cus_tamara", "first_name": "Tamara", "last_name": "Cruz"}], "ambiguous": False, "count": 1}

    def balance(args: BalanceInput) -> dict[str, Any]:
        return {"customer_id": args.customer_id, "balance": 9680.04, "open_count": 14, "currency": "USD"}

    def appointments(args: AppointmentsInput) -> dict[str, Any]:
        return {"customer_id": args.customer_id, "appointments": [{"status": "scheduled", "summary": "Pending schedule"}]}

    def semantic(args: SemanticInput) -> dict[str, Any]:
        results = []
        for index, content in enumerate(note_texts, start=1):
            results.append({"note_id": f"nte_{index}", "job_id": f"job_{index}", "customer_id": "cus_tamara", "content": content, "similarity_score": 0.9 - (index * 0.1)})
        return {"results": results, "count": len(results)}

    def get_note(args: GetNoteInput) -> dict[str, Any]:
        mapping = {
            "nte_1": {"note_id": "nte_1", "job_id": "job_1", "customer_id": "cus_tamara", "content": note_texts[0]},
            "nte_2": {"note_id": "nte_2", "job_id": "job_2", "customer_id": "cus_tamara", "content": note_texts[1] if len(note_texts) > 1 else note_texts[0]},
        }
        return mapping[args.note_id]

    return ToolRegistry(
        [
            ToolDefinition("search_customers", "", SearchCustomersInput, search),
            ToolDefinition("get_customer_balance", "", BalanceInput, balance),
            ToolDefinition("get_upcoming_appointments", "", AppointmentsInput, appointments),
            ToolDefinition("semantic_search_notes", "", SemanticInput, semantic),
            ToolDefinition("get_note", "", GetNoteInput, get_note),
        ]
    )


class OpenAIStoreAndGroundingUXHotfixTests(unittest.TestCase):
    def test_openai_model_sends_store_true(self) -> None:
        fake_client = MagicMock()
        fake_choice = MagicMock()
        fake_choice.message.content = "done"
        fake_choice.message.tool_calls = []
        fake_response = MagicMock()
        fake_response.output = []
        fake_response.output_text = "done"
        fake_client.responses.create.return_value = fake_response

        model = OpenAIToolCallingModel(api_key="sk-test", model_name="gpt-test")
        model._client = fake_client  # type: ignore[attr-defined]

        result = model.run(messages=[{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}], tools=[{"name": "tool", "description": "desc", "input_schema": {"type": "object"}}])

        self.assertEqual(result.message, "done")
        kwargs = fake_client.responses.create.call_args.kwargs
        self.assertTrue(kwargs["store"])
        self.assertEqual(kwargs["model"], "gpt-test")
        self.assertEqual(kwargs["temperature"], 0)
        self.assertEqual(kwargs["instructions"], "rules")
        self.assertEqual(kwargs["input"], [{"type": "message", "role": "user", "content": "hi"}])
        self.assertEqual(kwargs["tools"][0]["name"], "tool")
        self.assertTrue(kwargs["tools"][0]["strict"])
        self.assertEqual(kwargs["tools"][0]["parameters"], {"type": "object"})
        self.assertNotIn("chat", fake_client.__dict__)

    def test_structured_balance_answer_stays_clean(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "get_customer_balance", {})], message=None),
            ModelResponse(message="Tamara Cruz currently owes $9,680.04."),
        ]
        agent = AgentOrchestrator(model=FakeModel(scripted), registry=build_registry(["A note about refrigerant recovery."]), session_service=InMemorySessionService(), action_service=InMemoryActionService(), max_tool_rounds=8, max_history_messages=20)

        result = agent.run("How much does Tamara Cruz currently owe?", finalize_session_on_return=False)

        self.assertIn("$9,680.04", result.response)
        self.assertNotIn("available notes", result.response.lower())
        self.assertNotIn("causal relationship", result.response.lower())

    def test_structured_appointment_answer_stays_clean(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "get_upcoming_appointments", {"customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="Tamara has a pending schedule tomorrow morning."),
        ]
        agent = AgentOrchestrator(model=FakeModel(scripted), registry=build_registry(["A note about refrigerant recovery."]), session_service=InMemorySessionService(), action_service=InMemoryActionService(), max_tool_rounds=8, max_history_messages=20)

        result = agent.run("Does Tamara Cruz have any pending schedule?", finalize_session_on_return=False)

        self.assertIn("pending schedule", result.response.lower())
        self.assertNotIn("available notes", result.response.lower())
        self.assertNotIn("evidence does not establish", result.response.lower())

    def test_semantic_answer_still_applies_evidence_grounding(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant leak", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="The records show refrigerant-related issues but do not confirm a leak."),
        ]
        agent = AgentOrchestrator(model=FakeModel(scripted), registry=build_registry(["System may be low on refrigerant."]), session_service=InMemorySessionService(), action_service=InMemoryActionService(), max_tool_rounds=8, max_history_messages=20)

        result = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)

        self.assertIn("do not confirm", result.response.lower())

    def test_no_hvac_interpretation_in_note_summary(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "cooling issue", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="A technician recorded a liquid temperature of 180°F and noted that the water tower was not cooling the unit properly."),
        ]
        agent = AgentOrchestrator(model=FakeModel(scripted), registry=build_registry(["Liquid temperature was 180°F. Water tower was not cooling unit properly."]), session_service=InMemorySessionService(), action_service=InMemoryActionService(), max_tool_rounds=8, max_history_messages=20)

        result = agent.run("What did the note say?", finalize_session_on_return=False)

        lower = result.response.lower()
        self.assertIn("liquid temperature", lower)
        self.assertIn("water tower", lower)
        self.assertNotIn("refrigerant issues", lower)
        self.assertNotIn("refrigerant leak", lower)

    def test_user_facing_ids_hidden_by_default(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant leak", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="The records mention refrigerant recovery and a possible leak search."),
        ]
        agent = AgentOrchestrator(model=FakeModel(scripted), registry=build_registry(["System may be low on refrigerant."]), session_service=InMemorySessionService(), action_service=InMemoryActionService(), max_tool_rounds=8, max_history_messages=20)

        result = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)

        lower = result.response.lower()
        self.assertNotIn("note_id", lower)
        self.assertNotIn("job_id", lower)
        self.assertNotIn("customer_id", lower)

    def test_deterministic_note_followup_still_uses_get_note(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant leak", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="1. note one\n2. note two"),
            ModelResponse(tool_calls=[ModelToolCall("3", "get_note", {})], message=None),
            ModelResponse(message="details"),
        ]
        actions = InMemoryActionService()
        agent = AgentOrchestrator(model=FakeModel(scripted), registry=build_registry(["First note", "Second note"]), session_service=InMemorySessionService(), action_service=actions, max_tool_rounds=8, max_history_messages=20)

        first = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)
        second = agent.run("Tell me more about note 1.", session_id=first.session_id, finalize_session_on_return=False)

        self.assertEqual(actions.actions[2]["action_type"], "get_note")
        self.assertEqual(actions.actions[2]["arguments"]["note_id"], second.referenced_note_ids[0])


if __name__ == "__main__":
    unittest.main()