from __future__ import annotations

import types
import unittest
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import get_dashboard_service
from app.api.routes import dashboard as dashboard_routes
from app.main import app
from app.services.dashboard import DashboardServiceError


@dataclass
class FakeDashboardService:
    summary: dict[str, Any] | None = None
    sessions: list[dict[str, Any]] = field(default_factory=list)
    session_detail: dict[str, Any] | None = None
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    appointments: dict[str, Any] | None = None
    schedule_changes: dict[str, Any] | None = None
    last_appointments_args: dict[str, Any] | None = None
    last_schedule_change_args: dict[str, Any] | None = None
    detail_error: Exception | None = None

    def get_summary(self) -> dict[str, Any]:
        if self.summary is None:
            raise AssertionError("No summary configured")
        return self.summary

    def list_sessions(self, *, limit: int) -> list[dict[str, Any]]:
        return self.sessions[:limit]

    def get_session_detail(self, session_id: int) -> dict[str, Any]:
        if self.detail_error is not None:
            raise self.detail_error
        if self.session_detail is None:
            raise AssertionError("No session detail configured")
        return self.session_detail

    def list_handoffs(self, *, limit: int) -> list[dict[str, Any]]:
        return self.handoffs[:limit]

    def list_appointments(
        self,
        *,
        q: str | None,
        from_at,
        to_at,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self.last_appointments_args = {
            "q": q,
            "from_at": from_at,
            "to_at": to_at,
            "limit": limit,
            "offset": offset,
        }
        if self.appointments is None:
            raise AssertionError("No appointments configured")
        return self.appointments

    def list_schedule_changes(
        self,
        *,
        q: str | None,
        from_at,
        to_at,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        self.last_schedule_change_args = {
            "q": q,
            "from_at": from_at,
            "to_at": to_at,
            "limit": limit,
            "offset": offset,
        }
        if self.schedule_changes is None:
            raise AssertionError("No schedule changes configured")
        return self.schedule_changes


class DashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeDashboardService(
            summary={
                "customers": 732,
                "jobs": 1992,
                "ai_conversations": 124,
                "active_sessions": 4,
                "human_handoffs": 1,
                "upcoming_appointments": 0,
                "open_invoices": 221,
                "outstanding_balance": 497712.32,
                "recent_sessions": [],
                "appointments": [],
                "recent_schedule_changes": [],
                "outstanding_accounts": [],
            },
            sessions=[
                {
                    "session_id": 123,
                    "started_at": "2026-09-05T10:00:00Z",
                    "ended_at": None,
                    "channel": "web",
                    "status": "active",
                    "customer_id": "cus_tamara",
                    "customer_name": "Tamara Cruz",
                    "summary": "How much does Tamara Cruz currently owe?",
                    "message_count": 3,
                    "action_count": 2,
                    "needs_handoff": False,
                    "handoff_reason": None,
                }
            ],
            session_detail={
                "session": {
                    "session_id": 123,
                    "started_at": "2026-09-05T10:00:00Z",
                    "ended_at": None,
                    "channel": "web",
                    "status": "active",
                    "customer_id": "cus_tamara",
                    "customer_name": "Tamara Cruz",
                    "summary": "How much does Tamara Cruz currently owe?",
                    "message_count": 3,
                    "action_count": 2,
                    "needs_handoff": False,
                    "handoff_reason": None,
                },
                "conversation": [
                    {"role": "assistant", "content": "Welcome to Gulf Breeze Air. How can I help you today?", "tool_call_id": None},
                    {"role": "user", "content": "How much does Tamara Cruz currently owe?", "tool_call_id": None},
                    {"role": "assistant", "content": "Tamara Cruz currently owes $9,680.04.", "tool_call_id": None},
                ],
                "actions": [
                    {
                        "id": 1,
                        "timestamp": "2026-09-05T10:00:01Z",
                        "action_type": "get_customer_balance",
                        "display_label": "Checked outstanding balance",
                        "entity_type": "customer",
                        "entity_id": "cus_tamara",
                        "status": "ok",
                        "latency_ms": 4,
                        "arguments": {"customer_id": "cus_tamara"},
                        "result": {"ok": True},
                    }
                ],
            },
            handoffs=[
                {
                    "session_id": 124,
                    "started_at": "2026-09-05T11:00:00Z",
                    "ended_at": "2026-09-05T11:02:00Z",
                    "channel": "api",
                    "status": "handoff",
                    "customer_id": "cus_other",
                    "customer_name": "Casey Smith",
                    "summary": "Please reschedule my appointment.",
                    "message_count": 2,
                    "action_count": 1,
                    "needs_handoff": True,
                    "handoff_reason": "Unsupported mutation request",
                }
            ],
            appointments={
                "items": [
                    {
                        "job_id": "job_future_1",
                        "customer_name": "Tamara Cruz",
                        "description": "AC maintenance",
                        "address": "123 Main St, Miami, FL, 33101",
                        "technicians": ["Jordan Lee"],
                        "scheduled_start": "2026-09-09T13:00:00Z",
                        "scheduled_end": "2026-09-09T15:00:00Z",
                        "effective_start_at": "2026-09-10T14:00:00Z",
                        "effective_end_at": "2026-09-10T16:00:00Z",
                        "original_start_at": "2026-09-09T13:00:00Z",
                        "original_end_at": "2026-09-09T15:00:00Z",
                        "status": "scheduled",
                        "has_schedule_override": True,
                        "contact_summary": None,
                        "weather": None,
                    }
                ],
                "total": 1,
                "limit": 25,
                "offset": 0,
            },
            schedule_changes={
                "items": [
                    {
                        "change_id": 91,
                        "job_id": "job_future_1",
                        "customer_name": "Tamara Cruz",
                        "address": "123 Main St, Miami, FL, 33101",
                        "description": "AC maintenance",
                        "original_start_at": "2026-09-09T13:00:00Z",
                        "original_end_at": "2026-09-09T15:00:00Z",
                        "new_start_at": "2026-09-10T14:00:00Z",
                        "new_end_at": "2026-09-10T16:00:00Z",
                        "reason": "Customer requested afternoon",
                        "status": "active",
                        "created_at": "2026-09-05T11:30:00Z",
                        "agent_session_id": 123,
                        "channel": "web",
                        "changed_by": "Gulf Breeze AI · Web Chat",
                    }
                ],
                "total": 1,
                "limit": 25,
                "offset": 0,
            },
        )
        app.dependency_overrides[get_dashboard_service] = lambda: self.service
        self._original_get_settings = dashboard_routes.get_settings
        dashboard_routes.get_settings = lambda: types.SimpleNamespace(
            public_phone_enabled=False,
            public_phone_number="+19714586849",
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        dashboard_routes.get_settings = self._original_get_settings

    def test_summary_endpoint(self) -> None:
        response = self.client.get("/api/dashboard/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["customers"], 732)
        self.assertEqual(payload["jobs"], 1992)
        self.assertEqual(payload["open_invoices"], 221)

    def test_sessions_endpoint(self) -> None:
        response = self.client.get("/api/dashboard/sessions")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["channel"], "web")
        self.assertEqual(payload[0]["summary"], "How much does Tamara Cruz currently owe?")

    def test_session_detail_endpoint(self) -> None:
        response = self.client.get("/api/dashboard/sessions/123")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session"]["session_id"], 123)
        self.assertEqual(payload["conversation"][1]["role"], "user")
        self.assertEqual(payload["actions"][0]["display_label"], "Checked outstanding balance")

    def test_handoffs_endpoint(self) -> None:
        response = self.client.get("/api/dashboard/handoffs")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertTrue(payload[0]["needs_handoff"])

    def test_public_config_hides_phone_when_disabled(self) -> None:
        response = self.client.get("/api/public/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["public_phone_enabled"])
        self.assertIsNone(payload["public_phone_number"])

    def test_appointments_endpoint(self) -> None:
        response = self.client.get(
            "/api/dashboard/appointments?q=tamara&from=2026-09-01T00:00:00Z&to=2026-09-12T00:00:00Z&limit=25&offset=0"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["job_id"], "job_future_1")
        self.assertEqual(self.service.last_appointments_args["q"], "tamara")
        self.assertEqual(self.service.last_appointments_args["limit"], 25)

    def test_schedule_changes_endpoint(self) -> None:
        response = self.client.get(
            "/api/dashboard/schedule-changes?q=afternoon&from=2026-09-01T00:00:00Z&to=2026-09-12T00:00:00Z&limit=25&offset=0"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["change_id"], 91)
        self.assertEqual(self.service.last_schedule_change_args["q"], "afternoon")
        self.assertEqual(self.service.last_schedule_change_args["offset"], 0)

    def test_session_detail_not_found_returns_structured_error(self) -> None:
        self.service.detail_error = DashboardServiceError(
            code="session_not_found",
            message="The requested session was not found.",
            status_code=404,
        )

        response = self.client.get("/api/dashboard/sessions/999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "session_not_found")