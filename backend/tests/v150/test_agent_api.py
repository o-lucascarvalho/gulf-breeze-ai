from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import get_agent_service
from app.main import app
from app.services.agent_service import AgentServiceError


@dataclass
class FakeAgentService:
    next_session_id: int = 900
    chat_results: list[dict[str, Any]] = field(default_factory=list)
    sessions: dict[int, dict[str, Any]] = field(default_factory=dict)
    actions: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    chat_calls: list[dict[str, Any]] = field(default_factory=list)
    close_calls: list[int] = field(default_factory=list)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    chat_errors: dict[int | None, Exception] = field(default_factory=dict)
    close_errors: dict[int, Exception] = field(default_factory=dict)

    def create_session(self, *, caller_identifier: str | None = None, channel: str = "api") -> dict[str, Any]:
        self.create_calls.append({"caller_identifier": caller_identifier, "channel": channel})
        session_id = self.next_session_id
        self.next_session_id += 1
        session = {
            "session_id": session_id,
            "started_at": "2026-09-04T00:00:00Z",
            "ended_at": None,
            "channel": channel,
            "status": "active",
            "caller_identifier": caller_identifier,
            "customer_id": None,
            "customer_display_name": None,
            "context_summary": {
                "context_version": 1,
                "customer_id": None,
                "customer_display_name": None,
                "last_job_id": None,
                "last_invoice_id": None,
                "message_count": 0,
                "recent_note_references": [],
                "recent_job_references": [],
            },
            "metadata": {},
        }
        self.sessions[session_id] = session
        return session

    def chat(self, *, message: str, session_id: int | None, caller_identifier: str | None, debug: bool) -> dict[str, Any]:
        self.chat_calls.append(
            {
                "message": message,
                "session_id": session_id,
                "caller_identifier": caller_identifier,
                "debug": debug,
            }
        )
        error = self.chat_errors.get(session_id)
        if error is not None:
            raise error
        if not self.chat_results:
            raise AssertionError("No chat result configured")
        return self.chat_results.pop(0)

    def close_session(self, session_id: int) -> dict[str, Any]:
        self.close_calls.append(session_id)
        error = self.close_errors.get(session_id)
        if error is not None:
            raise error
        return {"session_id": session_id, "status": "completed"}

    def get_session(self, session_id: int) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            raise AssertionError("No session configured")
        return session

    def list_actions(self, *, session_id: int, limit: int) -> list[dict[str, Any]]:
        return list(self.actions.get(session_id, []))[:limit]

    def list_sessions(self, *, limit: int, status: str | None) -> list[dict[str, Any]]:
        sessions = list(self.sessions.values())
        if status is not None:
            sessions = [row for row in sessions if row["status"] == status]
        return sessions[:limit]


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeAgentService()
        app.dependency_overrides[get_agent_service] = lambda: self.service
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_new_session_chat_creates_session(self) -> None:
        self.service.chat_results.append(
            {
                "session_id": 101,
                "response": "Tamara Cruz currently owes $9,680.04.",
                "status": "active",
                "needs_handoff": False,
                "handoff_reason": None,
            }
        )

        response = self.client.post("/agent/chat", json={"message": "How much does Tamara Cruz owe?"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], 101)
        self.assertEqual(payload["status"], "active")
        self.assertEqual(self.service.chat_calls[0]["session_id"], None)
        self.assertEqual(self.service.chat_calls[0]["debug"], False)

    def test_explicit_session_creation(self) -> None:
        response = self.client.post("/agent/sessions", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "active")
        self.assertEqual(self.service.create_calls, [{"caller_identifier": None, "channel": "api"}])

    def test_explicit_session_creation_with_caller_identifier(self) -> None:
        response = self.client.post("/agent/sessions", json={"caller_identifier": "+15551234567"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.create_calls, [{"caller_identifier": "+15551234567", "channel": "api"}])

    def test_explicit_session_creation_with_channel(self) -> None:
        response = self.client.post("/agent/sessions", json={"channel": "web"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["channel"], "web")
        self.assertEqual(self.service.create_calls, [{"caller_identifier": None, "channel": "web"}])

    def test_existing_session_chat_reuses_session(self) -> None:
        self.service.chat_results.extend(
            [
                {
                    "session_id": 201,
                    "response": "First turn.",
                    "status": "active",
                    "needs_handoff": False,
                    "handoff_reason": None,
                },
                {
                    "session_id": 201,
                    "response": "Second turn.",
                    "status": "active",
                    "needs_handoff": False,
                    "handoff_reason": None,
                },
            ]
        )

        first = self.client.post("/agent/chat", json={"message": "Hi"})
        second = self.client.post(
            "/agent/chat",
            json={"session_id": first.json()["session_id"], "message": "Does she have any upcoming appointments?"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.service.chat_calls[1]["session_id"], 201)

    def test_context_continuity_keeps_same_session(self) -> None:
        self.service.chat_results.extend(
            [
                {
                    "session_id": 301,
                    "response": "Balance.",
                    "status": "active",
                    "needs_handoff": False,
                    "handoff_reason": None,
                },
                {
                    "session_id": 301,
                    "response": "Appointment.",
                    "status": "active",
                    "needs_handoff": False,
                    "handoff_reason": None,
                },
            ]
        )

        first = self.client.post("/agent/chat", json={"message": "How much does Tamara Cruz owe?"}).json()
        second = self.client.post("/agent/chat", json={"session_id": first["session_id"], "message": "Does she have an appointment?"})

        self.assertEqual(first["session_id"], 301)
        self.assertEqual(second.json()["session_id"], 301)
        self.assertEqual([call["session_id"] for call in self.service.chat_calls], [None, 301])

    def test_invalid_message_is_rejected(self) -> None:
        response = self.client.post("/agent/chat", json={"message": "   "})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_message")
        self.assertEqual(self.service.chat_calls, [])

    def test_handoff_response_is_preserved(self) -> None:
        self.service.chat_results.append(
            {
                "session_id": 401,
                "response": "I need office assistance to complete this request.",
                "status": "handoff",
                "needs_handoff": True,
                "handoff_reason": "Unsupported mutation request",
            }
        )

        response = self.client.post("/agent/chat", json={"message": "Move the appointment to Friday."})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["needs_handoff"])
        self.assertEqual(payload["status"], "handoff")

    def test_close_session(self) -> None:
        self.service.sessions[501] = {
            "session_id": 501,
            "started_at": "2026-09-04T00:00:00Z",
            "ended_at": None,
            "channel": "api",
            "status": "active",
            "caller_identifier": None,
            "customer_id": None,
            "customer_display_name": None,
            "context_summary": {
                "context_version": 1,
                "customer_id": None,
                "customer_display_name": None,
                "last_job_id": None,
                "last_invoice_id": None,
                "message_count": 0,
                "recent_note_references": [],
                "recent_job_references": [],
            },
            "metadata": {},
        }

        response = self.client.post("/agent/sessions/501/close")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(self.service.close_calls, [501])

    def test_session_inspection(self) -> None:
        self.service.sessions[601] = {
            "session_id": 601,
            "started_at": "2026-09-04T00:00:00Z",
            "ended_at": None,
            "channel": "api",
            "status": "active",
            "caller_identifier": "caller-1",
            "customer_id": "cus_tamara",
            "customer_display_name": "Tamara Cruz",
            "context_summary": {
                "context_version": 1,
                "customer_id": "cus_tamara",
                "customer_display_name": "Tamara Cruz",
                "last_job_id": "job_1",
                "last_invoice_id": None,
                "message_count": 2,
                "recent_note_references": [{"position": 1, "note_id": "nte_1", "job_id": "job_1", "customer_id": "cus_tamara"}],
                "recent_job_references": [],
            },
            "metadata": {"tool_round_limit": 8},
        }

        response = self.client.get("/agent/sessions/601")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], 601)
        self.assertEqual(payload["customer_id"], "cus_tamara")
        self.assertEqual(payload["context_summary"]["last_job_id"], "job_1")

    def test_actions_inspection(self) -> None:
        self.service.actions[701] = [
            {
                "id": 1,
                "timestamp": "2026-09-04T00:00:01Z",
                "action_type": "search_customers",
                "entity_type": "customer",
                "entity_id": None,
                "status": "ok",
                "latency_ms": 3,
                "arguments": {"name": "Tamara Cruz"},
                "result": {"ok": True},
            },
            {
                "id": 2,
                "timestamp": "2026-09-04T00:00:02Z",
                "action_type": "get_customer_balance",
                "entity_type": "customer",
                "entity_id": "cus_tamara",
                "status": "ok",
                "latency_ms": 4,
                "arguments": {"customer_id": "cus_tamara"},
                "result": {"ok": True},
            },
        ]
        self.service.sessions[701] = {
            "session_id": 701,
            "started_at": "2026-09-04T00:00:00Z",
            "ended_at": None,
            "channel": "api",
            "status": "active",
            "caller_identifier": None,
            "customer_id": None,
            "customer_display_name": None,
            "context_summary": {
                "context_version": 1,
                "customer_id": None,
                "customer_display_name": None,
                "last_job_id": None,
                "last_invoice_id": None,
                "message_count": 0,
                "recent_note_references": [],
                "recent_job_references": [],
            },
            "metadata": {},
        }

        response = self.client.get("/agent/sessions/701/actions?limit=10")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["action_type"] for item in payload], ["search_customers", "get_customer_balance"])

    def test_debug_mode_hides_details_by_default_and_exposes_sanitized_debug(self) -> None:
        self.service.chat_results.extend(
            [
                {
                    "session_id": 801,
                    "response": "Clean answer.",
                    "status": "active",
                    "needs_handoff": False,
                    "handoff_reason": None,
                },
                {
                    "session_id": 802,
                    "response": "Debug answer.",
                    "status": "active",
                    "needs_handoff": False,
                    "handoff_reason": None,
                    "tool_calls": [
                        {
                            "tool_name": "get_customer_balance",
                            "arguments": {"customer_id": "cus_tamara"},
                            "status": "ok",
                            "latency_ms": 3,
                            "error": None,
                        }
                    ],
                    "active_customer_id": "cus_tamara",
                    "active_job_id": "job_1",
                    "active_invoice_id": None,
                    "referenced_note_ids": ["nte_1"],
                },
            ]
        )

        normal = self.client.post("/agent/chat", json={"message": "Hi"})
        debug = self.client.post("/agent/chat", json={"message": "Hi again", "debug": True})

        self.assertEqual(normal.status_code, 200)
        self.assertNotIn("tool_calls", normal.json())
        self.assertNotIn("active_customer_id", normal.json())

        self.assertEqual(debug.status_code, 200)
        debug_payload = debug.json()
        self.assertIn("tool_calls", debug_payload)
        self.assertEqual(debug_payload["active_customer_id"], "cus_tamara")
        self.assertEqual(debug_payload["tool_calls"][0]["tool_name"], "get_customer_balance")

    def test_unknown_session_returns_structured_error(self) -> None:
        self.service.chat_errors[999] = AgentServiceError(
            code="session_not_found",
            message="The requested agent session was not found.",
            status_code=404,
        )
        response = self.client.post("/agent/chat", json={"session_id": 999, "message": "Hello"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "session_not_found")


if __name__ == "__main__":
    unittest.main()