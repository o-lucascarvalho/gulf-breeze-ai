from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.agents.agent import AgentOrchestrator
from app.agents.types import ModelResponse, ModelToolCall
from app.tools.registry import ToolRegistry
from app.tools.types import ToolDefinition


class SearchCustomersInput(BaseModel):
    name: str


class SemanticInput(BaseModel):
    query: str
    customer_id: str | None = None


class GetNoteInput(BaseModel):
    note_id: str


class GetJobInput(BaseModel):
    job_id: str


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


def build_registry() -> ToolRegistry:
    def search(args: SearchCustomersInput) -> dict[str, Any]:
        target = "cus_tamara" if args.name == "Tamara Cruz" else "cus_robert"
        first = "Tamara" if target == "cus_tamara" else "Robert"
        last = "Cruz" if target == "cus_tamara" else "Smith"
        return {
            "matches": [{"customer_id": target, "first_name": first, "last_name": last}],
            "ambiguous": False,
            "count": 1,
        }

    def semantic(args: SemanticInput) -> dict[str, Any]:
        return {
            "results": [
                {
                    "note_id": "nte_a",
                    "job_id": "job_a",
                    "customer_id": args.customer_id,
                    "content": "first note",
                    "similarity_score": 0.9,
                },
                {
                    "note_id": "nte_b",
                    "job_id": "job_b",
                    "customer_id": args.customer_id,
                    "content": "second note",
                    "similarity_score": 0.8,
                },
            ],
            "count": 2,
        }

    def get_note(args: GetNoteInput) -> dict[str, Any]:
        mapping = {
            "nte_a": {"note_id": "nte_a", "job_id": "job_a", "customer_id": "cus_tamara", "content": "first note"},
            "nte_b": {"note_id": "nte_b", "job_id": "job_b", "customer_id": "cus_tamara", "content": "second note"},
        }
        return mapping[args.note_id]

    def get_job(args: GetJobInput) -> dict[str, Any]:
        return {
            "found": True,
            "job": {
                "job_id": args.job_id,
                "customer": {"customer_id": "cus_tamara", "first_name": "Tamara", "last_name": "Cruz"},
                "description": "details",
            },
        }

    return ToolRegistry(
        [
            ToolDefinition("search_customers", "", SearchCustomersInput, search),
            ToolDefinition("semantic_search_notes", "", SemanticInput, semantic),
            ToolDefinition("get_note", "", GetNoteInput, get_note),
            ToolDefinition("get_job", "", GetJobInput, get_job),
        ]
    )


class DeterministicNoteFollowupUnitTests(unittest.TestCase):
    def _build(self, scripted: list[ModelResponse]):
        agent = AgentOrchestrator(
            model=FakeModel(scripted),
            registry=build_registry(),
            session_service=InMemorySessionService(),
            action_service=InMemoryActionService(),
            max_tool_rounds=8,
            max_history_messages=20,
        )
        return agent

    def test_note_1_followup_uses_get_note_not_semantic(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="1. first note\n2. second note"),
            ModelResponse(
                tool_calls=[ModelToolCall("3", "semantic_search_notes", {"query": "details about note 1"})],
                message=None,
            ),
            ModelResponse(message="detail"),
        ]
        session = InMemorySessionService()
        actions = InMemoryActionService()
        agent = AgentOrchestrator(
            model=FakeModel(scripted),
            registry=build_registry(),
            session_service=session,
            action_service=actions,
            max_tool_rounds=8,
            max_history_messages=20,
        )

        first = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)
        second = agent.run("Give me details about note 1.", session_id=first.session_id, finalize_session_on_return=False)

        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(actions.actions[2]["action_type"], "get_note")
        self.assertEqual(actions.actions[2]["arguments"]["note_id"], "nte_a")
        self.assertNotEqual(actions.actions[2]["action_type"], "semantic_search_notes")

    def test_second_reference_maps_to_second_note(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="1. first note\n2. second note"),
            ModelResponse(tool_calls=[ModelToolCall("3", "get_note", {})], message=None),
            ModelResponse(message="detail second"),
        ]
        actions = InMemoryActionService()
        agent = AgentOrchestrator(
            model=FakeModel(scripted),
            registry=build_registry(),
            session_service=InMemorySessionService(),
            action_service=actions,
            max_tool_rounds=8,
            max_history_messages=20,
        )

        first = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)
        second = agent.run("Tell me more about the second one.", session_id=first.session_id, finalize_session_on_return=False)

        self.assertEqual(actions.actions[2]["action_type"], "get_note")
        self.assertEqual(actions.actions[2]["arguments"]["note_id"], "nte_b")
        self.assertEqual(second.active_job_id, "job_b")

    def test_note_1_resolved_reference_overrides_stale_followup_defaults(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="1. first note\n2. second note"),
            ModelResponse(tool_calls=[ModelToolCall("3", "get_note", {})], message=None),
            ModelResponse(message="detail second"),
            ModelResponse(tool_calls=[ModelToolCall("4", "get_note", {})], message=None),
            ModelResponse(message="detail first"),
        ]
        actions = InMemoryActionService()
        agent = AgentOrchestrator(
            model=FakeModel(scripted),
            registry=build_registry(),
            session_service=InMemorySessionService(),
            action_service=actions,
            max_tool_rounds=8,
            max_history_messages=20,
        )

        first = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)
        second = agent.run("Tell me more about the second one.", session_id=first.session_id, finalize_session_on_return=False)
        third = agent.run("Give me details about note 1.", session_id=first.session_id, finalize_session_on_return=False)

        self.assertEqual(second.active_job_id, "job_b")
        self.assertEqual(actions.actions[2]["action_type"], "get_note")
        self.assertEqual(actions.actions[2]["arguments"]["note_id"], "nte_b")

        self.assertEqual(third.active_job_id, "job_a")
        self.assertEqual(actions.actions[3]["action_type"], "get_note")
        self.assertEqual(actions.actions[3]["arguments"]["note_id"], "nte_a")
        self.assertEqual(sum(1 for action in actions.actions if action["action_type"] == "semantic_search_notes"), 1)

    def test_context_continuity_and_customer_switch_regression(self) -> None:
        scripted = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "issues", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="listed"),
            ModelResponse(tool_calls=[ModelToolCall("3", "search_customers", {"name": "Robert Smith"})], message=None),
            ModelResponse(message="switched"),
        ]
        sessions = InMemorySessionService()
        actions = InMemoryActionService()
        agent = AgentOrchestrator(
            model=FakeModel(scripted),
            registry=build_registry(),
            session_service=sessions,
            action_service=actions,
            max_tool_rounds=8,
            max_history_messages=20,
        )

        first = agent.run("Has Tamara Cruz had issues?", finalize_session_on_return=False)
        second = agent.run("Now check Robert Smith.", session_id=first.session_id, finalize_session_on_return=False)

        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(second.active_customer_id, "cus_robert")
        self.assertEqual(second.referenced_note_ids, [])


if __name__ == "__main__":
    unittest.main()
