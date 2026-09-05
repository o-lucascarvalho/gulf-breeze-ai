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


def build_registry(note_texts: list[str]) -> ToolRegistry:
    def search(args: SearchCustomersInput) -> dict[str, Any]:
        return {
            "matches": [{"customer_id": "cus_tamara", "first_name": "Tamara", "last_name": "Cruz"}],
            "ambiguous": False,
            "count": 1,
        }

    def semantic(args: SemanticInput) -> dict[str, Any]:
        results = []
        for index, content in enumerate(note_texts, start=1):
            results.append(
                {
                    "note_id": f"nte_{index}",
                    "job_id": f"job_{index}",
                    "customer_id": "cus_tamara",
                    "content": content,
                    "similarity_score": 1.0 - (index * 0.1),
                }
            )
        return {"results": results, "count": len(results)}

    return ToolRegistry(
        [
            ToolDefinition("search_customers", "", SearchCustomersInput, search),
            ToolDefinition("semantic_search_notes", "", SemanticInput, semantic),
        ]
    )


def run_case(note_texts: list[str], final_message: str, user_message: str = "Has Tamara Cruz had refrigerant leaks before?") -> tuple[Any, InMemoryActionService]:
    scripted = [
        ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
        ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant leak", "customer_id": "cus_tamara"})], message=None),
        ModelResponse(message=final_message),
    ]
    actions = InMemoryActionService()
    agent = AgentOrchestrator(
        model=FakeModel(scripted),
        registry=build_registry(note_texts),
        session_service=InMemorySessionService(),
        action_service=actions,
        max_tool_rounds=8,
        max_history_messages=20,
    )
    result = agent.run(user_message, finalize_session_on_return=False)
    return result, actions


class EvidenceAwareGroundingUnitTests(unittest.TestCase):
    def test_explicit_confirmation_allows_yes(self) -> None:
        result, _ = run_case(
            ["Detected refrigerant leak at evaporator coil."],
            "Yes. A technician documented a refrigerant leak at the evaporator coil.",
        )

        lower = result.response.lower()
        self.assertIn("yes", lower)
        self.assertIn("refrigerant leak", lower)
        self.assertNotIn("do not clearly confirm", lower)

    def test_possible_or_suspected_preserves_uncertainty(self) -> None:
        result, _ = run_case(
            ["System may be low on refrigerant."],
            "Yes, the customer had a confirmed refrigerant leak.",
        )

        lower = result.response.lower()
        self.assertNotIn("confirmed refrigerant leak", lower)
        self.assertIn("may be low on refrigerant", lower)
        self.assertIn("possible issue", lower)

    def test_related_but_not_confirming_does_not_infer_leak(self) -> None:
        result, _ = run_case(
            ["Need to evacuate refrigerant before replacing reversing valve."],
            "Yes, the records confirm a refrigerant leak.",
        )

        lower = result.response.lower()
        self.assertNotIn("confirmed a refrigerant leak", lower)
        self.assertIn("related to the topic", lower)
        self.assertIn("evacuate refrigerant", lower)

    def test_explicit_negation_keeps_no_leak_found(self) -> None:
        result, _ = run_case(
            ["Electronic leak search performed; could not find leak."],
            "Yes, the leak was found and confirmed.",
        )

        lower = result.response.lower()
        self.assertNotIn("leak was found", lower)
        self.assertIn("do not confirm a leak", lower)
        self.assertIn("could not find leak", lower)

    def test_mixed_evidence_without_confirmation_is_not_yes(self) -> None:
        result, _ = run_case(
            [
                "System may be low on refrigerant.",
                "Refrigerant recovered for repair.",
                "Need to evacuate refrigerant before valve work.",
                "Cooling efficiency issue noted.",
            ],
            "Yes, the records confirm previous refrigerant leaks.",
        )

        lower = result.response.lower()
        self.assertFalse(lower.startswith("yes"))
        self.assertNotIn("confirmed previous refrigerant leaks", lower)
        self.assertIn("do not confirm the claim", lower)

    def test_mixed_evidence_with_one_explicit_confirmation_allows_yes(self) -> None:
        result, _ = run_case(
            [
                "System may be low on refrigerant.",
                "Refrigerant recovered for repair.",
                "Detected leak at evaporator coil.",
            ],
            "Yes, the customer had a refrigerant leak.",
        )

        self.assertEqual(result.response, "Yes, the customer had a refrigerant leak.")

    def test_causal_inference_regression(self) -> None:
        result, _ = run_case(
            ["Bad reversing valve."],
            "The reversing valve caused the refrigerant leak.",
        )

        self.assertIn("do not establish a causal relationship", result.response)

    def test_estimate_quote_does_not_become_failure(self) -> None:
        result, _ = run_case(
            ["Need quote for compressor replacement."],
            "The compressor failed.",
        )

        lower = result.response.lower()
        self.assertNotIn("the compressor failed", lower)
        self.assertIn("related to the topic", lower)
        self.assertIn("compressor replacement", lower)


if __name__ == "__main__":
    unittest.main()