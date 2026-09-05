from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.agents.agent import AgentOrchestrator
from app.agents.types import ModelResponse, ModelToolCall
from app.tools.registry import ToolRegistry
from app.tools.types import ToolDefinition


class EmptyInput(BaseModel):
    pass


class CustomerBalanceInput(BaseModel):
    customer_id: str


class SearchInput(BaseModel):
    name: str


class SemanticInput(BaseModel):
    query: str
    customer_id: str | None = None


class HandoffInput(BaseModel):
    reason: str


@dataclass
class FakeModel:
    scripted: list[ModelResponse]
    calls: int = 0

    def run(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        response = self.scripted[self.calls]
        self.calls += 1
        return response


class FakeSessionService:
    def __init__(self) -> None:
        self.created = []
        self.updated = []

    def create_session(self, *, channel: str, caller_identifier: str | None, metadata: dict | None) -> int:
        self.created.append({"channel": channel, "caller_identifier": caller_identifier, "metadata": metadata})
        return 42

    def get_session(self, session_id: int):
        return None

    def update_session(
        self,
        *,
        session_id: int,
        status: str,
        customer_id: str | None,
        metadata_updates: dict | None = None,
        mark_ended: bool = True,
    ) -> None:
        self.updated.append(
            {
                "session_id": session_id,
                "status": status,
                "customer_id": customer_id,
                "metadata_updates": metadata_updates,
                "mark_ended": mark_ended,
            }
        )


class FakeActionService:
    def __init__(self) -> None:
        self.actions = []

    def log_tool_call(self, **kwargs) -> None:
        self.actions.append(kwargs)


def build_registry() -> ToolRegistry:
    def get_customer_balance(args: CustomerBalanceInput) -> dict[str, Any]:
        return {
            "found": True,
            "customer_id": args.customer_id,
            "total_outstanding_balance": "125.50",
            "open_jobs_or_invoices": 1,
        }

    def search_customers(args: SearchInput) -> dict[str, Any]:
        if args.name == "John Smith":
            return {
                "matches": [
                    {"customer_id": "cus_1", "first_name": "John", "last_name": "Smith"},
                    {"customer_id": "cus_2", "first_name": "John", "last_name": "Smith"},
                ],
                "ambiguous": True,
                "count": 2,
            }
        if args.name == "Unknown":
            return {"matches": [], "ambiguous": False, "count": 0}
        return {
            "matches": [{"customer_id": "cus_1", "first_name": "John", "last_name": "Smith"}],
            "ambiguous": False,
            "count": 1,
        }

    def semantic_search(args: SemanticInput) -> dict[str, Any]:
        return {
            "results": [
                {
                    "note_id": "nte_1",
                    "job_id": "job_1",
                    "customer_id": args.customer_id,
                    "content": "refrigerant leak",
                    "similarity_score": 0.9,
                }
            ],
            "count": 1,
        }

    def request_handoff(args: HandoffInput) -> dict[str, Any]:
        return {"needs_handoff": True, "handoff_reason": args.reason}

    tools = [
        ToolDefinition(
            name="search_customers",
            description="search",
            input_model=SearchInput,
            handler=search_customers,
        ),
        ToolDefinition(
            name="get_customer_balance",
            description="balance",
            input_model=CustomerBalanceInput,
            handler=get_customer_balance,
        ),
        ToolDefinition(
            name="semantic_search_notes",
            description="semantic",
            input_model=SemanticInput,
            handler=semantic_search,
        ),
        ToolDefinition(
            name="request_handoff",
            description="handoff",
            input_model=HandoffInput,
            handler=request_handoff,
        ),
    ]
    return ToolRegistry(tools)


class AgentOrchestrationTests(unittest.TestCase):
    def _build_agent(self, scripted: list[ModelResponse], max_rounds: int = 8):
        session_service = FakeSessionService()
        action_service = FakeActionService()
        agent = AgentOrchestrator(
            model=FakeModel(scripted=scripted),
            registry=build_registry(),
            session_service=session_service,
            action_service=action_service,
            max_tool_rounds=max_rounds,
            max_history_messages=20,
        )
        return agent, session_service, action_service

    def test_direct_structured_question(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="1", name="get_customer_balance", arguments={"customer_id": "cus_1"})],
            ),
            ModelResponse(message="Customer owes 125.50."),
        ]
        agent, _, action_service = self._build_agent(scripted)
        result = agent.run("How much does customer owe?", channel="cli")

        self.assertEqual(result.status, "completed")
        self.assertFalse(result.needs_handoff)
        self.assertIn("125.50", result.response)
        self.assertEqual(action_service.actions[0]["action_type"], "get_customer_balance")

    def test_semantic_question(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[
                    ModelToolCall(
                        call_id="1",
                        name="semantic_search_notes",
                        arguments={"query": "refrigerant leaks", "customer_id": "cus_1"},
                    )
                ],
            ),
            ModelResponse(message="Yes, there is a note about refrigerant leak."),
        ]
        agent, _, action_service = self._build_agent(scripted)
        result = agent.run("Has this customer had refrigerant leaks?", channel="cli")

        self.assertEqual(result.status, "completed")
        self.assertEqual(action_service.actions[0]["action_type"], "semantic_search_notes")

    def test_multi_tool_question(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="1", name="search_customers", arguments={"name": "John Smith"})],
            ),
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="2", name="semantic_search_notes", arguments={"query": "last visit", "customer_id": "cus_1"})],
            ),
            ModelResponse(message="On the last visit there was a refrigerant leak."),
        ]
        agent, _, action_service = self._build_agent(scripted)
        result = agent.run("What happened on John's last visit?", channel="cli")

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.tool_calls), 2)
        self.assertEqual(action_service.actions[0]["action_type"], "search_customers")
        self.assertEqual(action_service.actions[1]["action_type"], "semantic_search_notes")

    def test_ambiguous_customer_no_arbitrary_selection(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="1", name="search_customers", arguments={"name": "John Smith"})],
            ),
            ModelResponse(message="I found multiple John Smith customers. Please share company or address."),
        ]
        agent, _, _ = self._build_agent(scripted)
        result = agent.run("What happened on John Smith's last visit?", channel="cli")
        self.assertIn("multiple", result.response.lower())

    def test_unknown_customer_grounded_response(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="1", name="search_customers", arguments={"name": "Unknown"})],
            ),
            ModelResponse(message="I could not find that customer in Gulf Breeze Air records."),
        ]
        agent, _, _ = self._build_agent(scripted)
        result = agent.run("How much does Unknown owe?", channel="cli")
        self.assertIn("could not find", result.response.lower())

    def test_unsupported_mutation_requests_handoff(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="1", name="request_handoff", arguments={"reason": "Appointment changes require office staff"})],
            ),
            ModelResponse(message="I will connect you with the office to change the appointment."),
        ]
        agent, session_service, _ = self._build_agent(scripted)
        result = agent.run("Cancel my appointment tomorrow.", channel="cli")

        self.assertTrue(result.needs_handoff)
        self.assertEqual(result.status, "handoff")
        self.assertEqual(session_service.updated[-1]["status"], "handoff")

    def test_tool_failure_handled(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="1", name="missing_tool", arguments={})],
            ),
            ModelResponse(message="I could not complete that lookup and need office assistance."),
        ]
        agent, _, action_service = self._build_agent(scripted)
        result = agent.run("Question", channel="cli")

        self.assertEqual(len(action_service.actions), 1)
        self.assertEqual(action_service.actions[0]["status"], "error")
        self.assertIn("assistance", result.response.lower())

    def test_max_rounds_guard(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id=str(i), name="search_customers", arguments={"name": "John"})],
            )
            for i in range(20)
        ]
        agent, session_service, _ = self._build_agent(scripted, max_rounds=3)
        result = agent.run("loop", channel="cli")

        self.assertEqual(result.status, "handoff")
        self.assertTrue(result.needs_handoff)
        self.assertIn("exceeded", result.handoff_reason)
        self.assertEqual(session_service.updated[-1]["status"], "handoff")

    def test_action_audit_and_session_lifecycle(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="1", name="get_customer_balance", arguments={"customer_id": "cus_1"})],
            ),
            ModelResponse(message="done"),
        ]
        agent, session_service, action_service = self._build_agent(scripted)
        result = agent.run("How much owed", channel="cli", caller_identifier="caller-1")

        self.assertEqual(session_service.created[0]["channel"], "cli")
        self.assertEqual(session_service.created[0]["caller_identifier"], "caller-1")
        self.assertEqual(session_service.updated[-1]["status"], "completed")
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(action_service.actions), 1)


if __name__ == "__main__":
    unittest.main()
