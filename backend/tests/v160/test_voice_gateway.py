from __future__ import annotations

import json
import unittest
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.api.dependencies import get_voice_gateway
from app.main import app
from app.services.agent_api_client import AgentApiClientError
from app.services.openai_realtime import OpenAIRealtimeSidebandManager, RealtimeClientError
from app.services.voice_gateway import FALLBACK_VOICE_ERROR_TEXT, VoiceGateway, VoiceGatewayError, sanitize_for_voice


def invalid_status_response(*, status_code: int, code: str | None, message: str) -> InvalidStatus:
    body_payload: dict[str, Any] = {
        "error": {
            "message": message,
            "type": "invalid_request_error",
            "code": code,
            "param": "",
        }
    }
    response = Response(
        status_code=status_code,
        reason_phrase="Not Found" if status_code == 404 else "Error",
        headers=Headers([("Content-Type", "application/json")]),
        body=json.dumps(body_payload).encode("utf-8"),
    )
    return InvalidStatus(response)


@dataclass
class FakeSocketConnection:
    recv_payloads: list[Any] = field(default_factory=lambda: [None])
    sent_messages: list[str] = field(default_factory=list)
    closed: bool = False

    def send(self, message: str) -> None:
        self.sent_messages.append(message)

    def recv(self) -> Any:
        if not self.recv_payloads:
            return None
        return self.recv_payloads.pop(0)

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeVoiceCallStore:
    next_id: int = 1
    rows: dict[int, dict[str, Any]] = field(default_factory=dict)

    def create_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
        agent_session_id: int,
        caller_identifier: str | None,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        call_id = self.next_id
        self.next_id += 1
        row = {
            "id": call_id,
            "provider": provider,
            "provider_call_id": provider_call_id,
            "agent_session_id": agent_session_id,
            "caller_identifier": caller_identifier,
            "status": status,
            "started_at": "started",
            "ended_at": None,
            "metadata": metadata or {},
        }
        self.rows[call_id] = row
        return dict(row)

    def get_call(self, voice_call_id: int) -> dict[str, Any] | None:
        row = self.rows.get(voice_call_id)
        return dict(row) if row else None

    def get_by_provider_call_id(self, *, provider: str, provider_call_id: str) -> dict[str, Any] | None:
        for row in self.rows.values():
            if row["provider"] == provider and row["provider_call_id"] == provider_call_id:
                return dict(row)
        return None

    def update_call(
        self,
        *,
        voice_call_id: int,
        status: str | None = None,
        ended_at: Any | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = self.rows.get(voice_call_id)
        if row is None:
            return None
        if status is not None:
            row["status"] = status
        if ended_at is not None:
            row["ended_at"] = ended_at
        if metadata_updates:
            merged = dict(row.get("metadata") or {})
            merged.update(metadata_updates)
            row["metadata"] = merged
        return dict(row)

    def list_calls(self, *, limit: int, status: str | None = None) -> list[dict[str, Any]]:
        rows = list(self.rows.values())
        if status:
            rows = [row for row in rows if row["status"] == status]
        return [dict(row) for row in rows[:limit]]


@dataclass
class FakeAgentApi:
    next_session_id: int = 1000
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    chat_calls: list[dict[str, Any]] = field(default_factory=list)
    close_calls: list[int] = field(default_factory=list)
    chat_responses: list[dict[str, Any]] = field(default_factory=list)
    create_error: AgentApiClientError | None = None
    chat_error: AgentApiClientError | None = None

    def create_session(self, *, caller_identifier: str | None = None) -> dict[str, Any]:
        self.create_calls.append({"caller_identifier": caller_identifier})
        if self.create_error:
            raise self.create_error
        session_id = self.next_session_id
        self.next_session_id += 1
        return {"session_id": session_id, "status": "active"}

    def chat(self, *, message: str, session_id: int) -> dict[str, Any]:
        self.chat_calls.append({"message": message, "session_id": session_id})
        if self.chat_error:
            raise self.chat_error
        if self.chat_responses:
            return self.chat_responses.pop(0)
        return {
            "session_id": session_id,
            "response": "Default response.",
            "status": "active",
            "needs_handoff": False,
            "handoff_reason": None,
        }

    def close_session(self, *, session_id: int) -> dict[str, Any]:
        self.close_calls.append(session_id)
        return {"session_id": session_id, "status": "completed"}


@dataclass
class FakeRealtimeClient:
    accept_calls: list[dict[str, Any]] = field(default_factory=list)
    speak_calls: list[dict[str, Any]] = field(default_factory=list)
    accept_error: RealtimeClientError | None = None
    speak_error: RealtimeClientError | None = None
    api_key: str = "test-api-key"
    project_id: str = "proj_test_project"

    def accept_call(self, *, call_id: str, instructions: str, caller_identifier: str | None = None) -> dict[str, Any]:
        self.accept_calls.append(
            {"call_id": call_id, "instructions": instructions, "caller_identifier": caller_identifier}
        )
        if self.accept_error:
            raise self.accept_error
        return {"ok": True}

    def speak_text(self, *, call_id: str, text: str) -> dict[str, Any]:
        self.speak_calls.append({"call_id": call_id, "text": text})
        if self.speak_error:
            raise self.speak_error
        return {"ok": True}

    def credential_scope(self) -> tuple[str, str]:
        return self.api_key, self.project_id


@dataclass
class FakeSidebandManager:
    open_calls: list[dict[str, Any]] = field(default_factory=list)
    send_calls: list[dict[str, Any]] = field(default_factory=list)
    close_calls: list[str] = field(default_factory=list)
    connected_handlers: dict[str, Any] = field(default_factory=dict)
    transcript_handlers: dict[str, Any] = field(default_factory=dict)
    disconnect_handlers: dict[str, Any] = field(default_factory=dict)
    api_key: str = "test-api-key"
    project_id: str = "proj_test_project"

    def open_call(
        self,
        *,
        call_id: str,
        accepted_at_utc: str | None = None,
        accepted_at_monotonic: float | None = None,
        on_connected,
        on_completed_transcript,
        on_disconnected,
    ) -> None:
        self.open_calls.append({"call_id": call_id})
        self.connected_handlers[call_id] = on_connected
        self.transcript_handlers[call_id] = on_completed_transcript
        self.disconnect_handlers[call_id] = on_disconnected

    def send_text(self, *, call_id: str, text: str) -> None:
        self.send_calls.append({"call_id": call_id, "text": text})

    def close_call(self, *, call_id: str) -> None:
        self.close_calls.append(call_id)

    def emit_connected(self, *, call_id: str) -> None:
        handler = self.connected_handlers[call_id]
        handler(call_id)

    def emit_transcript(self, *, call_id: str, text: str) -> None:
        handler = self.transcript_handlers[call_id]
        handler(call_id, text)

    def emit_disconnect(self, *, call_id: str, reason: str = "sideband_closed") -> None:
        handler = self.disconnect_handlers[call_id]
        handler(call_id, reason)

    def describe_connection_target(self, *, call_id: str) -> dict[str, Any]:
        return {
            "call_id": call_id,
            "url": f"wss://api.openai.com/v1/realtime?call_id={call_id}",
            "project_id": self.project_id,
            "project_header_present": bool(self.project_id),
        }

    def credential_scope(self) -> tuple[str, str]:
        return self.api_key, self.project_id


def incoming(
    call_id: str,
    caller: str = "+15551234567",
    twilio_sid: str | None = None,
    event_id: str = "evt_test",
    to_project_id: str = "proj_test_project",
    sip_call_id: str = "sip-call-123@example.com",
) -> dict[str, Any]:
    effective_twilio_sid = twilio_sid or f"CA_{call_id}"
    return {
        "id": event_id,
        "object": "event",
        "created_at": 1788618467,
        "type": "realtime.call.incoming",
        "data": {
            "call_id": call_id,
            "sip_headers": [
                {
                    "name": "From",
                    "value": f'"{caller}" <sip:{caller}@pstn.twilio.com>',
                },
                {
                    "name": "X-Twilio-CallSid",
                    "value": effective_twilio_sid,
                },
                {
                    "name": "To",
                    "value": f"sip:{to_project_id}@sip.api.openai.com;transport=tls",
                },
                {
                    "name": "Call-ID",
                    "value": sip_call_id,
                },
            ],
        },
    }


class VoiceGatewayTests(unittest.TestCase):
    def _build(
        self,
        *,
        greeting_enabled: bool = True,
        greeting_text: str = "Welcome to Gulf Breeze Air. How can I help you today?",
    ) -> tuple[VoiceGateway, FakeVoiceCallStore, FakeAgentApi, FakeRealtimeClient, FakeSidebandManager]:
        calls = FakeVoiceCallStore()
        agent = FakeAgentApi()
        realtime = FakeRealtimeClient()
        sideband = FakeSidebandManager()
        gateway = VoiceGateway(
            enabled=True,
            greeting_enabled=greeting_enabled,
            greeting_text=greeting_text,
            voice_calls=calls,
            agent_api=agent,
            realtime_client=realtime,
            sideband=sideband,
        )
        return gateway, calls, agent, realtime, sideband

    def test_incoming_call_webhook_real_payload_creates_mapping_accepts_and_opens_sideband(self) -> None:
        gateway, calls, agent, realtime, sideband = self._build()

        result = gateway.handle_openai_webhook(incoming("rtc_test_123"))

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(agent.create_calls, [{"caller_identifier": "+15551234567"}])
        self.assertEqual(realtime.accept_calls[0]["call_id"], "rtc_test_123")
        self.assertEqual(sideband.open_calls[0]["call_id"], "rtc_test_123")
        self.assertEqual(realtime.accept_calls[0]["call_id"], sideband.open_calls[0]["call_id"])
        created = calls.get_call(result["voice_call_id"])
        self.assertIsNotNone(created)
        self.assertEqual(created["provider_call_id"], "rtc_test_123")
        self.assertEqual(created["agent_session_id"], result["agent_session_id"])
        self.assertEqual(created["metadata"]["twilio_call_sid"], "CA_rtc_test_123")
        self.assertEqual(created["metadata"]["sip_call_id"], "sip-call-123@example.com")
        self.assertEqual(created["metadata"]["sip_project_id"], "proj_test_project")
        self.assertEqual(created["metadata"]["configured_project_id"], "proj_test_project")
        self.assertTrue(created["metadata"]["project_match"])
        self.assertTrue(created["metadata"]["accept_and_sideband_share_credentials"])

    def test_missing_nested_call_id_returns_structured_error(self) -> None:
        gateway, _, agent, _, _ = self._build()

        with self.assertRaises(VoiceGatewayError) as ctx:
            gateway.handle_openai_webhook({"type": "realtime.call.incoming", "data": {}})

        self.assertEqual(ctx.exception.code, "missing_call_id")
        self.assertEqual(agent.create_calls, [])

    def test_sip_headers_are_optional_for_parsing(self) -> None:
        gateway, calls, _, _, _ = self._build()

        result = gateway.handle_openai_webhook(
            {
                "id": "evt_no_headers",
                "object": "event",
                "created_at": 1788618467,
                "type": "realtime.call.incoming",
                "data": {"call_id": "rtc_no_headers", "sip_headers": []},
            }
        )

        row = calls.get_call(result["voice_call_id"])
        self.assertEqual(row["provider_call_id"], "rtc_no_headers")
        self.assertIsNone(row["caller_identifier"])

    def test_sideband_two_turns_reuse_same_agent_session(self) -> None:
        gateway, _, agent, _, sideband = self._build()

        gateway.handle_openai_webhook(incoming("rtc_two_turns"))
        sideband.emit_connected(call_id="rtc_two_turns")
        sideband.emit_transcript(call_id="rtc_two_turns", text="How much does Tamara Cruz owe?")
        sideband.emit_transcript(call_id="rtc_two_turns", text="Does she have any appointments?")

        self.assertEqual(agent.chat_calls[0]["session_id"], agent.chat_calls[1]["session_id"])
        self.assertEqual(agent.chat_calls[0]["message"], "How much does Tamara Cruz owe?")
        self.assertEqual(agent.chat_calls[1]["message"], "Does she have any appointments?")

    def test_agent_api_forwarding_uses_exact_sideband_transcript_text(self) -> None:
        gateway, _, agent, _, sideband = self._build()
        gateway.handle_openai_webhook(incoming("rtc_forward"))
        sideband.emit_connected(call_id="rtc_forward")

        sideband.emit_transcript(call_id="rtc_forward", text="How much does Tamara Cruz owe?")

        self.assertEqual(agent.chat_calls[0]["message"], "How much does Tamara Cruz owe?")

    def test_agent_response_is_emitted_via_sideband_and_sanitized(self) -> None:
        gateway, _, agent, _, sideband = self._build()
        gateway.handle_openai_webhook(incoming("rtc_sanitize"))
        sideband.emit_connected(call_id="rtc_sanitize")
        agent.chat_responses.append(
            {
                "session_id": 1000,
                "response": "Tamara Cruz owes **$9,680.04** and `balance` is due.",
                "status": "active",
                "needs_handoff": False,
                "handoff_reason": None,
            }
        )

        sideband.emit_transcript(call_id="rtc_sanitize", text="How much does Tamara Cruz owe?")

        self.assertEqual(sideband.send_calls[-1]["text"], "Tamara Cruz owes $9,680.04 and balance is due.")

    def test_handoff_marks_call_and_produces_caller_response(self) -> None:
        gateway, calls, agent, _, sideband = self._build()
        incoming_result = gateway.handle_openai_webhook(incoming("rtc_handoff"))
        sideband.emit_connected(call_id="rtc_handoff")
        agent.chat_responses.append(
            {
                "session_id": 1000,
                "response": "I need office assistance to complete this request.",
                "status": "handoff",
                "needs_handoff": True,
                "handoff_reason": "Unsupported mutation request",
            }
        )

        sideband.emit_transcript(call_id="rtc_handoff", text="Move my appointment.")

        record = calls.get_call(incoming_result["voice_call_id"])
        self.assertEqual(record["status"], "handoff")
        self.assertIn("office assistance", sideband.send_calls[-1]["text"])

    def test_agent_api_failure_uses_safe_fallback_and_marks_failed(self) -> None:
        gateway, calls, agent, _, sideband = self._build()
        incoming_result = gateway.handle_openai_webhook(incoming("rtc_fail"))
        sideband.emit_connected(call_id="rtc_fail")
        agent.chat_error = AgentApiClientError(
            code="agent_api_timeout",
            message="Timed out while contacting the Agent API.",
            status_code=504,
        )

        sideband.emit_transcript(call_id="rtc_fail", text="How much does Tamara owe?")

        self.assertEqual(sideband.send_calls[-1]["text"], FALLBACK_VOICE_ERROR_TEXT)
        record = calls.get_call(incoming_result["voice_call_id"])
        self.assertEqual(record["status"], "failed")

    def test_sideband_disconnect_marks_completed_and_closes_agent_session(self) -> None:
        gateway, calls, agent, _, sideband = self._build()
        incoming_result = gateway.handle_openai_webhook(incoming("rtc_end"))
        sideband.emit_connected(call_id="rtc_end")

        sideband.emit_disconnect(call_id="rtc_end", reason="remote_hangup")

        self.assertEqual(agent.close_calls, [incoming_result["agent_session_id"]])
        row = calls.get_call(incoming_result["voice_call_id"])
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(sideband.close_calls[-1], "rtc_end")

    def test_multiple_calls_map_to_distinct_sessions(self) -> None:
        gateway, calls, _, _, _ = self._build()

        first = gateway.handle_openai_webhook(incoming("rtc_8"))
        second = gateway.handle_openai_webhook(incoming("rtc_9"))

        self.assertNotEqual(first["agent_session_id"], second["agent_session_id"])
        self.assertNotEqual(first["voice_call_id"], second["voice_call_id"])
        self.assertEqual(calls.get_call(first["voice_call_id"])["provider_call_id"], "rtc_8")
        self.assertEqual(calls.get_call(second["voice_call_id"])["provider_call_id"], "rtc_9")

    def test_accepted_call_id_equals_sideband_call_id(self) -> None:
        gateway, _, _, realtime, sideband = self._build()

        gateway.handle_openai_webhook(incoming("rtc_same_id"))

        self.assertEqual(realtime.accept_calls[0]["call_id"], sideband.open_calls[0]["call_id"])

    def test_later_duplicate_webhook_cannot_alter_running_sideband_call_id(self) -> None:
        gateway, calls, _, realtime, sideband = self._build()

        first = gateway.handle_openai_webhook(incoming("rtc_original", twilio_sid="CA_shared"))
        sideband.emit_connected(call_id="rtc_original")
        second = gateway.handle_openai_webhook(incoming("rtc_duplicate", twilio_sid="CA_shared", event_id="evt_dup"))

        self.assertTrue(second["idempotent"])
        self.assertEqual(len(realtime.accept_calls), 1)
        self.assertEqual(len(sideband.open_calls), 1)
        self.assertEqual(sideband.open_calls[0]["call_id"], "rtc_original")
        self.assertEqual(calls.get_call(first["voice_call_id"])["provider_call_id"], "rtc_original")

    def test_same_twilio_sid_different_rtc_ids_does_not_race_mutable_mapping(self) -> None:
        gateway, calls, agent, _, sideband = self._build()

        first = gateway.handle_openai_webhook(incoming("rtc_a", twilio_sid="CA_race"))
        duplicate = gateway.handle_openai_webhook(incoming("rtc_b", twilio_sid="CA_race", event_id="evt_b"))
        sideband.emit_connected(call_id="rtc_a")

        row = calls.get_call(first["voice_call_id"])
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(first["voice_call_id"], duplicate["voice_call_id"])
        self.assertEqual(first["agent_session_id"], duplicate["agent_session_id"])
        self.assertEqual(row["provider_call_id"], "rtc_a")
        self.assertEqual(len(agent.create_calls), 1)
        self.assertEqual(len(sideband.open_calls), 1)
        self.assertEqual(sideband.open_calls[0]["call_id"], "rtc_a")

    def test_realtime_accept_failure_marks_failed(self) -> None:
        gateway, calls, _, realtime, _ = self._build()
        realtime.accept_error = RealtimeClientError(
            code="realtime_http_error",
            message="Could not accept call",
            status_code=502,
        )

        with self.assertRaises(VoiceGatewayError):
            gateway.handle_openai_webhook(incoming("rtc_10"))

        row = calls.get_by_provider_call_id(provider="openai_realtime", provider_call_id="rtc_10")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "failed")

    def test_webhook_ignores_non_incoming_event_types(self) -> None:
        gateway, _, _, _, _ = self._build()

        result = gateway.handle_openai_webhook(
            {
                "id": "evt_ignored",
                "object": "event",
                "created_at": 1788618467,
                "type": "realtime.call.ended",
                "data": {"call_id": "rtc_ignored"},
            }
        )

        self.assertEqual(result["event"], "ignored")
        self.assertEqual(result["reason"], "webhook_only_supports_realtime_call_incoming")

    def test_sip_project_id_matches_configured_project(self) -> None:
        gateway, calls, _, _, _ = self._build()

        result = gateway.handle_openai_webhook(incoming("rtc_project_match", to_project_id="proj_test_project"))

        row = calls.get_call(result["voice_call_id"])
        self.assertEqual(row["metadata"]["sip_project_id"], "proj_test_project")
        self.assertEqual(row["metadata"]["configured_project_id"], "proj_test_project")
        self.assertTrue(row["metadata"]["project_match"])

    def test_project_mismatch_fails_clearly(self) -> None:
        gateway, calls, agent, realtime, sideband = self._build()

        with self.assertRaises(VoiceGatewayError) as ctx:
            gateway.handle_openai_webhook(incoming("rtc_project_mismatch", to_project_id="proj_other"))

        self.assertEqual(ctx.exception.code, "voice_project_mismatch")
        self.assertEqual(agent.create_calls, [])
        self.assertEqual(realtime.accept_calls, [])
        self.assertEqual(sideband.open_calls, [])
        self.assertEqual(calls.rows, {})

    def test_call_is_not_active_until_sideband_connected(self) -> None:
        gateway, calls, _, _, sideband = self._build()

        result = gateway.handle_openai_webhook(incoming("rtc_stage"))
        record_before = calls.get_call(result["voice_call_id"])
        self.assertEqual(record_before["status"], "accepted")
        self.assertEqual(sideband.send_calls, [])

        sideband.emit_connected(call_id="rtc_stage")
        record_after = calls.get_call(result["voice_call_id"])
        self.assertEqual(record_after["status"], "active")

    def test_call_stays_accepted_until_eventual_sideband_success(self) -> None:
        gateway, calls, _, _, sideband = self._build()

        result = gateway.handle_openai_webhook(incoming("rtc_retry_lifecycle"))
        before_connect = calls.get_call(result["voice_call_id"])
        self.assertEqual(before_connect["status"], "accepted")

        sideband.emit_connected(call_id="rtc_retry_lifecycle")
        after_connect = calls.get_call(result["voice_call_id"])
        self.assertEqual(after_connect["status"], "active")

    def test_greeting_sent_after_sideband_connected(self) -> None:
        gateway, calls, agent, _, sideband = self._build()

        result = gateway.handle_openai_webhook(incoming("rtc_greeting"))
        self.assertEqual(sideband.send_calls, [])

        sideband.emit_connected(call_id="rtc_greeting")

        self.assertEqual(sideband.send_calls[0]["call_id"], "rtc_greeting")
        self.assertEqual(sideband.send_calls[0]["text"], "Welcome to Gulf Breeze Air. How can I help you today?")
        self.assertEqual(agent.chat_calls, [])
        row = calls.get_call(result["voice_call_id"])
        self.assertTrue(row["metadata"]["greeting_sent"])

    def test_greeting_not_sent_before_sideband_connection(self) -> None:
        gateway, _, agent, _, sideband = self._build()

        gateway.handle_openai_webhook(incoming("rtc_no_early_greeting"))

        self.assertEqual(sideband.send_calls, [])
        self.assertEqual(agent.chat_calls, [])

    def test_greeting_disabled_by_configuration(self) -> None:
        gateway, calls, _, _, sideband = self._build(greeting_enabled=False)

        result = gateway.handle_openai_webhook(incoming("rtc_greeting_disabled"))
        sideband.emit_connected(call_id="rtc_greeting_disabled")

        self.assertEqual(sideband.send_calls, [])
        row = calls.get_call(result["voice_call_id"])
        self.assertNotIn("greeting_sent", row["metadata"])

    def test_configured_greeting_text_used_exactly(self) -> None:
        custom_text = "Welcome to Gulf Breeze Air. How can I help you today?"
        gateway, _, _, _, sideband = self._build(greeting_text=custom_text)

        gateway.handle_openai_webhook(incoming("rtc_custom_greeting"))
        sideband.emit_connected(call_id="rtc_custom_greeting")

        self.assertEqual(sideband.send_calls[0]["text"], custom_text)

    def test_duplicate_incoming_events_do_not_produce_duplicate_greeting(self) -> None:
        gateway, calls, _, _, sideband = self._build()

        first = gateway.handle_openai_webhook(incoming("rtc_greet_a", twilio_sid="CA_greet_shared"))
        duplicate = gateway.handle_openai_webhook(incoming("rtc_greet_b", twilio_sid="CA_greet_shared", event_id="evt_greet_dup"))

        self.assertTrue(duplicate["idempotent"])
        sideband.emit_connected(call_id="rtc_greet_a")
        self.assertEqual(len(sideband.send_calls), 1)

        row = calls.get_call(first["voice_call_id"])
        self.assertTrue(row["metadata"]["greeting_sent"])

    def test_greeting_sent_once_after_eventual_sideband_success(self) -> None:
        gateway, calls, _, _, sideband = self._build()

        result = gateway.handle_openai_webhook(incoming("rtc_greeting_retry"))
        self.assertEqual(sideband.send_calls, [])

        sideband.emit_connected(call_id="rtc_greeting_retry")
        sideband.emit_connected(call_id="rtc_greeting_retry")

        self.assertEqual(len(sideband.send_calls), 1)
        row = calls.get_call(result["voice_call_id"])
        self.assertTrue(row["metadata"]["greeting_sent"])

    def test_greeting_is_not_sent_through_agent_api(self) -> None:
        gateway, _, agent, _, sideband = self._build()

        gateway.handle_openai_webhook(incoming("rtc_no_agent_greeting"))
        sideband.emit_connected(call_id="rtc_no_agent_greeting")

        self.assertEqual(agent.chat_calls, [])

    def test_greeting_failure_does_not_fail_the_whole_call(self) -> None:
        gateway, calls, _, _, sideband = self._build()
        result = gateway.handle_openai_webhook(incoming("rtc_greeting_fail"))

        original_send_text = sideband.send_text

        def failing_send_text(*, call_id: str, text: str) -> None:
            raise RealtimeClientError(
                code="sideband_send_failed",
                message="Greeting send failed",
                status_code=502,
            )

        sideband.send_text = failing_send_text
        sideband.emit_connected(call_id="rtc_greeting_fail")
        sideband.send_text = original_send_text

        row = calls.get_call(result["voice_call_id"])
        self.assertEqual(row["status"], "active")
        self.assertNotIn("greeting_sent", row["metadata"])

    def test_sideband_connect_failure_marks_failed_and_closes_agent_session(self) -> None:
        gateway, calls, agent, _, sideband = self._build()

        result = gateway.handle_openai_webhook(incoming("rtc_sideband_fail"))
        sideband.emit_disconnect(call_id="rtc_sideband_fail", reason="sideband_connect_failed:InvalidStatus")

        row = calls.get_call(result["voice_call_id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn(result["agent_session_id"], agent.close_calls)

    def test_exhausted_call_id_not_found_retries_mark_call_failed_and_close_agent_session(self) -> None:
        gateway, calls, agent, _, sideband = self._build()

        result = gateway.handle_openai_webhook(incoming("rtc_retry_exhausted_gateway"))
        sideband.emit_disconnect(
            call_id="rtc_retry_exhausted_gateway",
            reason="sideband_connect_failed:http_404:call_id_not_found",
        )

        row = calls.get_call(result["voice_call_id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn(result["agent_session_id"], agent.close_calls)


class VoiceWebhookApiTests(unittest.TestCase):
    def test_invalid_webhook_shape_returns_structured_client_error(self) -> None:
        gateway = VoiceGateway(
            enabled=True,
            greeting_enabled=True,
            greeting_text="Welcome to Gulf Breeze Air. How can I help you today?",
            voice_calls=FakeVoiceCallStore(),
            agent_api=FakeAgentApi(),
            realtime_client=FakeRealtimeClient(),
            sideband=FakeSidebandManager(),
        )
        app.dependency_overrides[get_voice_gateway] = lambda: gateway
        client = TestClient(app)

        response = client.post("/voice/openai/webhook", json={})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_event")
        app.dependency_overrides.clear()


class MarkdownSanitizerTests(unittest.TestCase):
    def test_markdown_sanitizer_removes_format_tokens(self) -> None:
        spoken = sanitize_for_voice("Amount is **$9,680.04** on `invoice` line.")
        self.assertEqual(spoken, "Amount is $9,680.04 on invoice line.")


class SipIdentityExtractionTests(unittest.TestCase):
    def test_extracts_sip_call_id(self) -> None:
        payload = incoming("rtc_sip_id", sip_call_id="abc123@sip.example.com")

        sip_call_id = VoiceGateway._extract_sip_call_id(payload)

        self.assertEqual(sip_call_id, "abc123@sip.example.com")

    def test_sip_call_id_is_not_confused_with_twilio_call_sid(self) -> None:
        payload = incoming(
            "rtc_sip_identity",
            twilio_sid="CA_twilio_123",
            sip_call_id="sip-leg-999@sip.example.com",
        )

        self.assertEqual(VoiceGateway._extract_twilio_call_sid(payload), "CA_twilio_123")
        self.assertEqual(VoiceGateway._extract_sip_call_id(payload), "sip-leg-999@sip.example.com")
        self.assertNotEqual(VoiceGateway._extract_twilio_call_sid(payload), VoiceGateway._extract_sip_call_id(payload))


class RealtimeDiagnosticTests(unittest.TestCase):
    def test_sideband_404_diagnostic_preserves_safe_status_headers_and_body(self) -> None:
        response = Response(
            status_code=404,
            reason_phrase="Not Found",
            headers=Headers(
                [
                    ("Content-Type", "application/json"),
                    ("X-Request-Id", "req_123"),
                    ("Set-Cookie", "secret-cookie"),
                ]
            ),
            body=b'{"error":"call not found"}',
        )
        exc = InvalidStatus(response)

        diagnostics = OpenAIRealtimeSidebandManager._invalid_status_diagnostics(exc)

        self.assertEqual(diagnostics["http_status"], 404)
        self.assertEqual(diagnostics["classification"], "call_not_found")
        self.assertEqual(diagnostics["response_body"], '{"error":"call not found"}')
        self.assertEqual(diagnostics["response_headers"]["Content-Type"], "application/json")
        self.assertEqual(diagnostics["response_headers"]["X-Request-Id"], "req_123")
        self.assertEqual(diagnostics["openai_request_id"], "req_123")
        self.assertNotIn("Set-Cookie", diagnostics["response_headers"])

    def test_404_call_id_not_found_is_classified_for_retry(self) -> None:
        exc = invalid_status_response(
            status_code=404,
            code="call_id_not_found",
            message="No session found for the provided call_id",
        )

        diagnostics = OpenAIRealtimeSidebandManager._invalid_status_diagnostics(exc)

        self.assertEqual(diagnostics["provider_error_code"], "call_id_not_found")
        self.assertEqual(diagnostics["classification"], "call_id_not_found")


class RealtimeSidebandRetryTests(unittest.TestCase):
    def _build_manager(self, connect_attempts: list[Any], sleep_calls: list[float]) -> OpenAIRealtimeSidebandManager:
        attempts = list(connect_attempts)

        def fake_connect(url: str, additional_headers: dict[str, str]) -> FakeSocketConnection:
            outcome = attempts.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        return OpenAIRealtimeSidebandManager(
            api_key="test-api-key",
            project_id="proj_test_project",
            model="gpt-realtime",
            voice="alloy",
            instructions="test instructions",
            retry_delays_ms=(200, 400, 800, 1200),
            _connect=fake_connect,
            _sleep=fake_sleep,
        )

    def test_first_sideband_attempt_call_id_not_found_second_succeeds(self) -> None:
        sleep_calls: list[float] = []
        connection = FakeSocketConnection()
        manager = self._build_manager(
            [
                invalid_status_response(
                    status_code=404,
                    code="call_id_not_found",
                    message="No session found for the provided call_id",
                ),
                connection,
            ],
            sleep_calls,
        )
        connected_calls: list[str] = []
        disconnected_reasons: list[str] = []

        manager._run_call_loop(
            call_id="rtc_retry_once",
            on_connected=lambda call_id: connected_calls.append(call_id),
            on_completed_transcript=lambda *_args: None,
            on_disconnected=lambda _call_id, reason: disconnected_reasons.append(reason),
        )

        self.assertEqual(connected_calls, ["rtc_retry_once"])
        self.assertEqual(sleep_calls, [0.2])
        self.assertEqual(disconnected_reasons, ["sideband_closed"])

    def test_multiple_temporary_failures_then_success(self) -> None:
        sleep_calls: list[float] = []
        manager = self._build_manager(
            [
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                FakeSocketConnection(),
            ],
            sleep_calls,
        )
        connected_calls: list[str] = []

        manager._run_call_loop(
            call_id="rtc_retry_multi",
            on_connected=lambda call_id: connected_calls.append(call_id),
            on_completed_transcript=lambda *_args: None,
            on_disconnected=lambda *_args: None,
        )

        self.assertEqual(connected_calls, ["rtc_retry_multi"])
        self.assertEqual(sleep_calls, [0.2, 0.4])

    def test_retry_delays_backoff_invoked(self) -> None:
        sleep_calls: list[float] = []
        manager = self._build_manager(
            [
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                FakeSocketConnection(),
            ],
            sleep_calls,
        )

        manager._run_call_loop(
            call_id="rtc_retry_backoff",
            on_connected=lambda *_args: None,
            on_completed_transcript=lambda *_args: None,
            on_disconnected=lambda *_args: None,
        )

        self.assertEqual(sleep_calls, [0.2, 0.4, 0.8, 1.2])

    def test_max_retry_count_respected(self) -> None:
        sleep_calls: list[float] = []
        manager = self._build_manager(
            [
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
                invalid_status_response(status_code=404, code="call_id_not_found", message="No session found for the provided call_id"),
            ],
            sleep_calls,
        )
        disconnected_reasons: list[str] = []
        connected_calls: list[str] = []

        manager._run_call_loop(
            call_id="rtc_retry_exhausted",
            on_connected=lambda call_id: connected_calls.append(call_id),
            on_completed_transcript=lambda *_args: None,
            on_disconnected=lambda _call_id, reason: disconnected_reasons.append(reason),
        )

        self.assertEqual(connected_calls, [])
        self.assertEqual(sleep_calls, [0.2, 0.4, 0.8, 1.2])
        self.assertEqual(disconnected_reasons, ["sideband_connect_failed:http_404:call_id_not_found"])

    def test_arbitrary_404_does_not_retry(self) -> None:
        sleep_calls: list[float] = []
        manager = self._build_manager(
            [invalid_status_response(status_code=404, code="other_code", message="Some other not found")],
            sleep_calls,
        )
        disconnected_reasons: list[str] = []

        manager._run_call_loop(
            call_id="rtc_no_retry_404",
            on_connected=lambda *_args: None,
            on_completed_transcript=lambda *_args: None,
            on_disconnected=lambda _call_id, reason: disconnected_reasons.append(reason),
        )

        self.assertEqual(sleep_calls, [])
        self.assertEqual(disconnected_reasons, ["sideband_connect_failed:http_404:invalid_endpoint_or_call_not_found"])

    def test_401_403_500_do_not_use_special_retry_path(self) -> None:
        for status_code, expected_reason in [
            (401, "sideband_connect_failed:http_401:project_or_auth_mismatch"),
            (403, "sideband_connect_failed:http_403:project_or_auth_mismatch"),
            (500, "sideband_connect_failed:http_500:unknown"),
        ]:
            with self.subTest(status_code=status_code):
                sleep_calls: list[float] = []
                manager = self._build_manager(
                    [invalid_status_response(status_code=status_code, code="provider_error", message="Provider error")],
                    sleep_calls,
                )
                disconnected_reasons: list[str] = []

                manager._run_call_loop(
                    call_id=f"rtc_status_{status_code}",
                    on_connected=lambda *_args: None,
                    on_completed_transcript=lambda *_args: None,
                    on_disconnected=lambda _call_id, reason: disconnected_reasons.append(reason),
                )

                self.assertEqual(sleep_calls, [])
                self.assertEqual(disconnected_reasons, [expected_reason])

    def test_retry_helper_only_retries_call_id_not_found_404(self) -> None:
        manager = OpenAIRealtimeSidebandManager(
            api_key="test-api-key",
            project_id="proj_test_project",
            model="gpt-realtime",
            voice="alloy",
            instructions="test instructions",
        )

        self.assertEqual(
            manager._retry_delay_for(
                attempt=1,
                max_attempts=5,
                diagnostics={"http_status": 404, "provider_error_code": "call_id_not_found"},
            ),
            200,
        )
        self.assertIsNone(
            manager._retry_delay_for(
                attempt=1,
                max_attempts=5,
                diagnostics={"http_status": 404, "provider_error_code": "other"},
            )
        )
        self.assertIsNone(
            manager._retry_delay_for(
                attempt=1,
                max_attempts=5,
                diagnostics={"http_status": 500, "provider_error_code": "call_id_not_found"},
            )
        )


if __name__ == "__main__":
    unittest.main()