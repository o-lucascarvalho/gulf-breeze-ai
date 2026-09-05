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


class GetBalanceInput(BaseModel):
    customer_id: str


class UpcomingInput(BaseModel):
    customer_id: str


class SemanticInput(BaseModel):
    query: str
    customer_id: str | None = None
    top_k: int = 5


class GetJobInput(BaseModel):
    job_id: str


class GetInvoiceInput(BaseModel):
    job_id: str


@dataclass
class ScriptedModel:
    responses: list[ModelResponse]
    calls: int = 0
    seen_messages: list[list[dict[str, Any]]] | None = None

    def __post_init__(self) -> None:
        self.seen_messages = []

    def run(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        self.seen_messages.append(messages)
        response = self.responses[self.calls]
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
            "id": session_id,
            "channel": channel,
            "caller_identifier": caller_identifier,
            "status": "active",
            "customer_id": None,
            "metadata_json": metadata or {},
            "ended_at": None,
        }
        return session_id

    def get_session(self, session_id: int):
        data = self.records.get(session_id)
        if not data:
            return None

        class SessionRow:
            pass

        row = SessionRow()
        row.id = data["id"]
        row.channel = data["channel"]
        row.caller_identifier = data["caller_identifier"]
        row.status = data["status"]
        row.customer_id = data["customer_id"]
        row.metadata_json = data["metadata_json"]
        row.ended_at = data["ended_at"]
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
            merged = dict(row.get("metadata_json") or {})
            merged.update(metadata_updates)
            row["metadata_json"] = merged
        row["ended_at"] = "ended" if mark_ended else None


class InMemoryActionService:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []

    def log_tool_call(self, **kwargs) -> None:
        self.actions.append(kwargs)


def build_registry() -> ToolRegistry:
    def search_customers(args: SearchCustomersInput) -> dict[str, Any]:
        if args.name == "Tamara Cruz":
            return {
                "matches": [
                    {
                        "customer_id": "cus_tamara",
                        "first_name": "Tamara",
                        "last_name": "Cruz",
                        "company": None,
                    }
                ],
                "ambiguous": False,
                "count": 1,
            }
        return {
            "matches": [
                {
                    "customer_id": "cus_robert",
                    "first_name": "Robert",
                    "last_name": "Smith",
                    "company": None,
                }
            ],
            "ambiguous": False,
            "count": 1,
        }

    def get_balance(args: GetBalanceInput) -> dict[str, Any]:
        return {
            "found": True,
            "customer_id": args.customer_id,
            "total_outstanding_balance": "88.00",
            "open_jobs_or_invoices": 1,
        }

    def get_upcoming(args: UpcomingInput) -> dict[str, Any]:
        return {
            "found": True,
            "customer_id": args.customer_id,
            "appointments": [],
            "count": 0,
        }

    def semantic(args: SemanticInput) -> dict[str, Any]:
        return {
            "results": [
                {
                    "note_id": "nte_1",
                    "job_id": "job_1",
                    "customer_id": args.customer_id,
                    "content": "possible leak found, could not locate leak",
                    "similarity_score": 0.8,
                },
                {
                    "note_id": "nte_2",
                    "job_id": "job_2",
                    "customer_id": args.customer_id,
                    "content": "bad reversing valve",
                    "similarity_score": 0.7,
                },
            ],
            "count": 2,
        }

    def get_job(args: GetJobInput) -> dict[str, Any]:
        return {
            "found": True,
            "job": {
                "job_id": args.job_id,
                "customer": {
                    "customer_id": "cus_tamara" if args.job_id.startswith("job_") else "cus_robert",
                    "first_name": "Tamara",
                    "last_name": "Cruz",
                    "company": None,
                },
                "description": "job details",
                "employees": [{"first_name": "Zoe", "last_name": "Hoffman"}],
            },
        }

    def get_job_invoice(args: GetInvoiceInput) -> dict[str, Any]:
        return {
            "found": True,
            "job_id": args.job_id,
            "invoice": {
                "invoice_id": "inv_1",
                "invoice_number": "100",
                "due_amount": "40.00",
                "amount": "120.00",
                "line_items": [],
            },
        }

    tools = [
        ToolDefinition("search_customers", "search", SearchCustomersInput, search_customers),
        ToolDefinition("get_customer_balance", "balance", GetBalanceInput, get_balance),
        ToolDefinition("get_upcoming_appointments", "upcoming", UpcomingInput, get_upcoming),
        ToolDefinition("semantic_search_notes", "semantic", SemanticInput, semantic),
        ToolDefinition("get_job", "job", GetJobInput, get_job),
        ToolDefinition("get_job_invoice", "invoice", GetInvoiceInput, get_job_invoice),
    ]
    return ToolRegistry(tools)


class SessionContextUnitTests(unittest.TestCase):
    def _build(self, responses: list[ModelResponse], max_history: int = 20):
        model = ScriptedModel(responses)
        session_service = InMemorySessionService()
        action_service = InMemoryActionService()
        agent = AgentOrchestrator(
            model=model,
            registry=build_registry(),
            session_service=session_service,
            action_service=action_service,
            max_tool_rounds=8,
            max_history_messages=max_history,
        )
        return agent, model, session_service, action_service

    def test_session_reuse_and_customer_context(self) -> None:
        responses = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "get_customer_balance", {"customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="She owes 88.00."),
            ModelResponse(tool_calls=[ModelToolCall("3", "get_upcoming_appointments", {})], message=None),
            ModelResponse(message="No upcoming appointments."),
        ]
        agent, _, sessions, actions = self._build(responses)

        first = agent.run("How much does Tamara Cruz owe?", finalize_session_on_return=False)
        second = agent.run(
            "Does she have any upcoming appointments?",
            session_id=first.session_id,
            finalize_session_on_return=False,
        )

        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(sessions.records[first.session_id]["status"], "active")
        self.assertEqual(actions.actions[-1]["action_type"], "get_upcoming_appointments")
        self.assertEqual(actions.actions[-1]["arguments"]["customer_id"], "cus_tamara")

    def test_note_reference_follow_up_and_ordinals(self) -> None:
        responses = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant leak", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="1. possible leak\n2. bad reversing valve"),
            ModelResponse(tool_calls=[ModelToolCall("3", "get_job", {})], message=None),
            ModelResponse(message="Details about note 1 from job_1."),
            ModelResponse(tool_calls=[ModelToolCall("4", "get_job", {"job_id": "job_2"})], message=None),
            ModelResponse(message="Details about second one from job_2."),
        ]
        agent, model, _, actions = self._build(responses)

        first = agent.run("Has Tamara Cruz had refrigerant leaks before?", finalize_session_on_return=False)
        second = agent.run("Tell me more about note 1", session_id=first.session_id, finalize_session_on_return=False)
        third = agent.run("What about the second one?", session_id=first.session_id, finalize_session_on_return=False)

        self.assertEqual(actions.actions[2]["action_type"], "get_job")
        self.assertEqual(actions.actions[2]["arguments"]["job_id"], "job_1")
        self.assertEqual(actions.actions[3]["arguments"]["job_id"], "job_2")

        follow_up_user_message = [m for m in model.seen_messages[3] if m.get("role") == "user"][-1]["content"]
        self.assertIn("Resolved note reference", follow_up_user_message)
        self.assertEqual(second.active_customer_id, "cus_tamara")
        self.assertEqual(third.active_customer_id, "cus_tamara")

    def test_job_and_invoice_follow_up(self) -> None:
        responses = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "get_job", {"job_id": "job_1"})], message=None),
            ModelResponse(message="Last visit details."),
            ModelResponse(tool_calls=[ModelToolCall("3", "get_job", {})], message=None),
            ModelResponse(message="Zoe worked on that visit."),
            ModelResponse(tool_calls=[ModelToolCall("4", "get_job_invoice", {})], message=None),
            ModelResponse(message="Still due is 40.00."),
        ]
        agent, _, _, actions = self._build(responses)

        first = agent.run("What happened on Tamara Cruz's last visit?", finalize_session_on_return=False)
        second = agent.run("Who worked on that visit?", session_id=first.session_id, finalize_session_on_return=False)
        third = agent.run("How much was that invoice still due?", session_id=first.session_id, finalize_session_on_return=False)

        self.assertEqual(actions.actions[2]["arguments"]["job_id"], "job_1")
        self.assertEqual(actions.actions[3]["arguments"]["job_id"], "job_1")
        self.assertEqual(third.active_invoice_id, "inv_1")
        self.assertEqual(second.active_job_id, "job_1")

    def test_customer_switch_clears_stale_references(self) -> None:
        responses = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "cooling", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(message="Two notes found."),
            ModelResponse(tool_calls=[ModelToolCall("3", "search_customers", {"name": "Robert Smith"})], message=None),
            ModelResponse(message="Now focused on Robert Smith."),
        ]
        agent, _, sessions, _ = self._build(responses)

        first = agent.run("Has Tamara Cruz had cooling issues?", finalize_session_on_return=False)
        second = agent.run("Now check Robert Smith.", session_id=first.session_id, finalize_session_on_return=False)

        self.assertEqual(second.active_customer_id, "cus_robert")
        self.assertEqual(second.referenced_note_ids, [])
        metadata = sessions.records[first.session_id]["metadata_json"]["conversation_context"]
        self.assertIsNone(metadata["last_job_id"])

    def test_no_context_pronoun_does_not_guess(self) -> None:
        responses = [
            ModelResponse(tool_calls=[ModelToolCall("1", "get_customer_balance", {})], message=None),
            ModelResponse(message="Please tell me which customer you mean."),
        ]
        agent, _, _, actions = self._build(responses)

        result = agent.run("How much does she owe?")

        self.assertEqual(actions.actions[0]["status"], "error")
        self.assertIn("which customer", result.response.lower())

    def test_grounding_and_uncertainty_safeguards(self) -> None:
        responses = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(tool_calls=[ModelToolCall("2", "semantic_search_notes", {"query": "refrigerant leak", "customer_id": "cus_tamara"})], message=None),
            ModelResponse(
                message=(
                    "The reversing valve caused the refrigerant leak and it was a confirmed leak at the condenser."
                )
            ),
        ]
        agent, _, _, _ = self._build(responses)

        result = agent.run("Has Tamara had refrigerant leaks?")

        self.assertIn("do not establish a causal relationship", result.response)
        self.assertIn("do not confirm the leak location", result.response)

    def test_message_history_bound(self) -> None:
        responses = []
        for i in range(6):
            responses.append(ModelResponse(message=f"answer {i}"))
        agent, _, sessions, _ = self._build(responses, max_history=4)

        session_id = None
        for i in range(6):
            result = agent.run(f"msg {i}", session_id=session_id, finalize_session_on_return=False)
            session_id = result.session_id

        messages = sessions.records[session_id]["metadata_json"]["conversation_context"]["messages"]
        self.assertLessEqual(len(messages), 4)

    def test_session_lifecycle_and_audit_continuity(self) -> None:
        responses = [
            ModelResponse(tool_calls=[ModelToolCall("1", "search_customers", {"name": "Tamara Cruz"})], message=None),
            ModelResponse(message="ok"),
            ModelResponse(tool_calls=[ModelToolCall("2", "get_customer_balance", {})], message=None),
            ModelResponse(message="balance"),
        ]
        agent, _, sessions, actions = self._build(responses)

        first = agent.run("Hello", finalize_session_on_return=False)
        second = agent.run("How much does she owe?", session_id=first.session_id, finalize_session_on_return=False)
        agent.close_session(first.session_id)

        self.assertEqual(first.session_id, second.session_id)
        self.assertTrue(all(row["session_id"] == first.session_id for row in actions.actions))
        self.assertEqual(sessions.records[first.session_id]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
