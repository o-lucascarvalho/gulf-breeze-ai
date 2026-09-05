from __future__ import annotations

import logging
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.agent_api_client import AgentApiClient, AgentApiClientError
from app.services.openai_realtime import OpenAIRealtimeClient, RealtimeClientError
from app.services.voice_calls import VoiceCallService

logger = logging.getLogger("uvicorn.error")

VOICE_PROVIDER = "openai_realtime"
VOICE_INSTRUCTIONS = (
    "You are the voice interface for Gulf Breeze Air. "
    "Speak clearly and naturally in concise sentences. "
    "Do not answer company-record questions from your own knowledge. "
    "User turns must be routed through the Gulf Breeze Agent API and read back accurately. "
    "Do not invent business facts. "
    "If the agent indicates handoff, tell the caller office staff will need to assist."
)

FALLBACK_VOICE_ERROR_TEXT = (
    "I'm having trouble accessing the office system right now. "
    "Please contact the office for assistance."
)


@dataclass(frozen=True)
class VoiceGatewayError(Exception):
    code: str
    message: str
    status_code: int


def _log_kv(event: str, **fields: Any) -> str:
    rendered: list[str] = [event]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered_value = str(value).lower()
        elif isinstance(value, (dict, list, tuple)):
            rendered_value = json.dumps(value, sort_keys=True)
        elif isinstance(value, str) and any(char.isspace() for char in value):
            rendered_value = json.dumps(value)
        else:
            rendered_value = str(value)
        rendered.append(f"{key}={rendered_value}")
    return " ".join(rendered)


class VoiceGateway:
    def __init__(
        self,
        *,
        enabled: bool,
        greeting_enabled: bool,
        greeting_text: str,
        voice_calls: VoiceCallService,
        agent_api: AgentApiClient,
        realtime_client: OpenAIRealtimeClient,
        sideband,
    ) -> None:
        self._enabled = enabled
        self._greeting_enabled = greeting_enabled
        self._greeting_text = greeting_text
        self._voice_calls = voice_calls
        self._agent_api = agent_api
        self._realtime_client = realtime_client
        self._sideband = sideband

    def handle_openai_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled:
            raise VoiceGatewayError(code="voice_disabled", message="Voice agent is disabled.", status_code=503)

        if not isinstance(payload, dict):
            raise VoiceGatewayError(code="invalid_webhook", message="Webhook payload must be a JSON object.", status_code=422)

        event_type = payload.get("type")
        if not isinstance(event_type, str) or not event_type.strip():
            raise VoiceGatewayError(code="invalid_event", message="Missing event type.", status_code=422)

        if event_type == "realtime.call.incoming":
            return self._handle_incoming_call(payload)

        return {
            "ok": True,
            "event": "ignored",
            "reason": "webhook_only_supports_realtime_call_incoming",
            "type": event_type,
        }

    def get_call(self, voice_call_id: int) -> dict[str, Any] | None:
        return self._voice_calls.get_call(voice_call_id)

    def list_calls(self, *, limit: int, status: str | None) -> list[dict[str, Any]]:
        return self._voice_calls.list_calls(limit=limit, status=status)

    def _handle_incoming_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        received_at_utc = datetime.now(timezone.utc)
        received_at_monotonic = time.perf_counter()
        event_id = self._extract_event_id(payload)
        call_id = self._extract_call_id(payload)
        caller_identifier = self._extract_caller_identifier(payload)
        twilio_call_sid = self._extract_twilio_call_sid(payload)
        sip_call_id = self._extract_sip_call_id(payload)
        sip_project_id = self._extract_sip_project_id(payload)
        sip_from = self._extract_sip_header_value(payload, "from")
        sip_to = self._extract_sip_header_value(payload, "to")
        configured_project_id = self._configured_project_id()
        project_match = sip_project_id == configured_project_id if sip_project_id and configured_project_id else None

        logger.info(
            _log_kv(
                "voice.incoming_received",
                utc_ts=received_at_utc.isoformat(),
                monotonic_ms=int(received_at_monotonic * 1000),
                event_id=event_id,
                call_id=call_id,
                openai_call_id=call_id,
                twilio_call_sid=twilio_call_sid,
                sip_call_id=sip_call_id,
                caller_number=caller_identifier,
                sip_from=sip_from,
                sip_to=sip_to,
                sip_project_id=sip_project_id,
                configured_project_id=configured_project_id,
                project_match=project_match,
            )
        )

        if sip_project_id and configured_project_id and sip_project_id != configured_project_id:
            logger.error(
                _log_kv(
                    "voice.project_mismatch",
                    event_id=event_id,
                    call_id=call_id,
                    sip_project_id=sip_project_id,
                    configured_project_id=configured_project_id,
                    project_match=False,
                )
            )
            raise VoiceGatewayError(
                code="voice_project_mismatch",
                message=(
                    "SIP destination project ID does not match configured OPENAI_PROJECT_ID. "
                    f"sip_project_id={sip_project_id} configured_project_id={configured_project_id}"
                ),
                status_code=409,
            )

        existing = self._find_existing_call(call_id=call_id, twilio_call_sid=twilio_call_sid)
        if existing:
            return {
                "ok": True,
                "event": "incoming",
                "voice_call_id": existing["id"],
                "agent_session_id": existing["agent_session_id"],
                "status": existing["status"],
                "idempotent": True,
            }

        try:
            session = self._agent_api.create_session(caller_identifier=caller_identifier)
            agent_session_id = int(session["session_id"])
            record = self._voice_calls.create_call(
                provider=VOICE_PROVIDER,
                provider_call_id=call_id,
                agent_session_id=agent_session_id,
                caller_identifier=caller_identifier,
                status="ringing",
                metadata={
                    "event_id": event_id,
                    "webhook_type": "realtime.call.incoming",
                    "twilio_call_sid": twilio_call_sid,
                    "sip_call_id": sip_call_id,
                    "sip_project_id": sip_project_id,
                    "configured_project_id": configured_project_id,
                    "project_match": project_match,
                },
            )
            logger.info(
                _log_kv(
                    "voice.call_correlated",
                    utc_ts=datetime.now(timezone.utc).isoformat(),
                    monotonic_ms=int(time.perf_counter() * 1000),
                    event_id=event_id,
                    call_id=call_id,
                    openai_call_id=call_id,
                    twilio_call_sid=twilio_call_sid,
                    sip_call_id=sip_call_id,
                    caller_number=caller_identifier,
                    sip_from=sip_from,
                    sip_to=sip_to,
                    voice_call_id=record["id"],
                    agent_session_id=agent_session_id,
                    sip_project_id=sip_project_id,
                    configured_project_id=configured_project_id,
                    project_match=project_match,
                )
            )
        except AgentApiClientError as exc:
            raise VoiceGatewayError(code=exc.code, message=exc.message, status_code=exc.status_code) from exc

        try:
            self._voice_calls.update_call(voice_call_id=record["id"], status="accepting")
            accept_started_utc = datetime.now(timezone.utc)
            accept_started_monotonic = time.perf_counter()
            logger.info(
                _log_kv(
                    "voice.accept_started",
                    utc_ts=accept_started_utc.isoformat(),
                    monotonic_ms=int(accept_started_monotonic * 1000),
                    elapsed_since_incoming_ms=int((accept_started_monotonic - received_at_monotonic) * 1000),
                    event_id=event_id,
                    call_id=call_id,
                    openai_call_id=call_id,
                    twilio_call_sid=twilio_call_sid,
                    sip_call_id=sip_call_id,
                    caller_number=caller_identifier,
                    sip_from=sip_from,
                    sip_to=sip_to,
                    voice_call_id=record["id"],
                    agent_session_id=agent_session_id,
                )
            )
            self._realtime_client.accept_call(call_id=call_id, instructions=VOICE_INSTRUCTIONS, caller_identifier=caller_identifier)
            accepted_call_id = call_id
            accept_succeeded_utc = datetime.now(timezone.utc)
            accept_succeeded_monotonic = time.perf_counter()
            record = self._voice_calls.update_call(voice_call_id=record["id"], status="accepted") or record
            self._voice_calls.update_call(
                voice_call_id=record["id"],
                metadata_updates={
                    "accepted_call_id": accepted_call_id,
                    "accept_and_sideband_share_credentials": self._credentials_match(),
                    "accept_started_utc": accept_started_utc.isoformat(),
                    "accept_succeeded_utc": accept_succeeded_utc.isoformat(),
                },
            )
            logger.info(
                _log_kv(
                    "voice.accept_succeeded",
                    utc_ts=accept_succeeded_utc.isoformat(),
                    monotonic_ms=int(accept_succeeded_monotonic * 1000),
                    elapsed_since_incoming_ms=int((accept_succeeded_monotonic - received_at_monotonic) * 1000),
                    event_id=event_id,
                    call_id=accepted_call_id,
                    openai_call_id=accepted_call_id,
                    twilio_call_sid=twilio_call_sid,
                    sip_call_id=sip_call_id,
                    caller_number=caller_identifier,
                    sip_from=sip_from,
                    sip_to=sip_to,
                    voice_call_id=record["id"],
                    agent_session_id=agent_session_id,
                    accept_and_sideband_share_credentials=self._credentials_match(),
                )
            )

            sideband_target = self._sideband.describe_connection_target(call_id=accepted_call_id)
            logger.info(
                _log_kv(
                    "voice.sideband_connect_started",
                    utc_ts=datetime.now(timezone.utc).isoformat(),
                    monotonic_ms=int(time.perf_counter() * 1000),
                    elapsed_since_accept_ms=int((time.perf_counter() - accept_succeeded_monotonic) * 1000),
                    event_id=event_id,
                    call_id=accepted_call_id,
                    openai_call_id=accepted_call_id,
                    twilio_call_sid=twilio_call_sid,
                    sip_call_id=sip_call_id,
                    caller_number=caller_identifier,
                    sip_from=sip_from,
                    sip_to=sip_to,
                    voice_call_id=record["id"],
                    agent_session_id=agent_session_id,
                    url=sideband_target["url"],
                    project_id=sideband_target["project_id"],
                    project_header_present=sideband_target["project_header_present"],
                )
            )
            self._sideband.open_call(
                call_id=accepted_call_id,
                accepted_at_utc=accept_succeeded_utc.isoformat(),
                accepted_at_monotonic=accept_succeeded_monotonic,
                on_connected=self._on_sideband_connected,
                on_completed_transcript=self._on_sideband_transcript,
                on_disconnected=self._on_sideband_disconnected,
            )
        except RealtimeClientError as exc:
            self._voice_calls.update_call(
                voice_call_id=record["id"],
                status="failed",
                metadata_updates={"accept_error": exc.message},
            )
            logger.error(
                _log_kv(
                    "voice.accept_failed",
                    utc_ts=datetime.now(timezone.utc).isoformat(),
                    event_id=event_id,
                    call_id=call_id,
                    openai_call_id=call_id,
                    twilio_call_sid=twilio_call_sid,
                    sip_call_id=sip_call_id,
                    voice_call_id=record["id"],
                    agent_session_id=agent_session_id,
                    error=exc.message,
                )
            )
            self._sideband.close_call(call_id=call_id)
            try:
                self._agent_api.close_session(session_id=agent_session_id)
            except AgentApiClientError:
                pass
            raise VoiceGatewayError(code=exc.code, message=exc.message, status_code=exc.status_code) from exc
        except Exception as exc:
            self._voice_calls.update_call(
                voice_call_id=record["id"],
                status="failed",
                metadata_updates={"sideband_error": str(exc)},
            )
            logger.error(
                _log_kv(
                    "voice.sideband_connect_failed",
                    utc_ts=datetime.now(timezone.utc).isoformat(),
                    event_id=event_id,
                    call_id=call_id,
                    openai_call_id=call_id,
                    twilio_call_sid=twilio_call_sid,
                    sip_call_id=sip_call_id,
                    voice_call_id=record["id"],
                    agent_session_id=agent_session_id,
                    error=str(exc),
                )
            )
            self._sideband.close_call(call_id=call_id)
            try:
                self._agent_api.close_session(session_id=agent_session_id)
            except AgentApiClientError:
                pass
            raise VoiceGatewayError(
                code="sideband_open_failed",
                message="Failed to establish sideband realtime connection.",
                status_code=502,
            ) from exc

        return {
            "ok": True,
            "event": "incoming",
            "voice_call_id": record["id"],
            "agent_session_id": agent_session_id,
            "status": record["status"],
        }

    def _on_sideband_connected(self, call_id: str) -> None:
        record = self._voice_calls.get_by_provider_call_id(provider=VOICE_PROVIDER, provider_call_id=call_id)
        if not record:
            return

        if record["status"] in {"failed", "completed"}:
            return

        record = self._voice_calls.update_call(
            voice_call_id=record["id"],
            status="active",
            metadata_updates={"sideband_connected": True},
        ) or record
        logger.info(
            _log_kv(
                "voice.sideband_connected",
                utc_ts=datetime.now(timezone.utc).isoformat(),
                monotonic_ms=int(time.perf_counter() * 1000),
                call_id=call_id,
                openai_call_id=call_id,
                twilio_call_sid=(record.get("metadata") or {}).get("twilio_call_sid"),
                sip_call_id=(record.get("metadata") or {}).get("sip_call_id"),
                voice_call_id=record["id"],
                agent_session_id=record["agent_session_id"],
            )
        )
        self._send_greeting_if_needed(call_id=call_id, record=record)

    def _send_greeting_if_needed(self, *, call_id: str, record: dict[str, Any]) -> None:
        metadata = record.get("metadata") or {}
        if not self._greeting_enabled:
            return
        if not self._greeting_text.strip():
            return
        if metadata.get("greeting_sent"):
            return

        log_extra = {
            "voice_call_id": record["id"],
            "openai_call_id": call_id,
            "twilio_call_sid": metadata.get("twilio_call_sid"),
        }
        logger.info(_log_kv("voice.greeting_started", **log_extra))
        try:
            self._sideband.send_text(call_id=call_id, text=self._greeting_text)
            self._voice_calls.update_call(
                voice_call_id=record["id"],
                metadata_updates={"greeting_sent": True, "greeting_text": self._greeting_text},
            )
            logger.info(_log_kv("voice.greeting_sent", **log_extra))
        except RealtimeClientError as exc:
            logger.error(_log_kv("voice.greeting_failed", **log_extra, error=exc.message))

    def _on_sideband_transcript(self, call_id: str, transcript: str) -> None:
        record = self._voice_calls.get_by_provider_call_id(provider=VOICE_PROVIDER, provider_call_id=call_id)
        if not record:
            logger.warning("sideband transcript for unknown call", extra={"provider_call_id": call_id})
            return

        if not transcript:
            return

        logger.info("user turn received", extra={"voice_call_id": record["id"]})

        started = time.perf_counter()
        try:
            agent_response = self._agent_api.chat(message=transcript, session_id=int(record["agent_session_id"]))
            agent_latency_ms = int((time.perf_counter() - started) * 1000)
            spoken_text = sanitize_for_voice(str(agent_response.get("response", "")))
            if not spoken_text:
                spoken_text = FALLBACK_VOICE_ERROR_TEXT

            self._sideband.send_text(call_id=call_id, text=spoken_text)
            logger.info("agent response received", extra={"voice_call_id": record["id"], "latency_ms": agent_latency_ms})

            next_status = "handoff" if bool(agent_response.get("needs_handoff")) else "active"
            self._voice_calls.update_call(
                voice_call_id=record["id"],
                status=next_status,
                metadata_updates={
                    "last_transcript": transcript,
                    "last_agent_status": agent_response.get("status"),
                    "last_handoff_reason": agent_response.get("handoff_reason"),
                    "last_agent_latency_ms": agent_latency_ms,
                },
            )
        except AgentApiClientError as exc:
            self._handle_turn_failure(record=record, call_id=call_id, error_message=exc.message)
        except RealtimeClientError as exc:
            self._handle_turn_failure(record=record, call_id=call_id, error_message=exc.message)

    def _on_sideband_disconnected(self, call_id: str, reason: str) -> None:
        record = self._voice_calls.get_by_provider_call_id(provider=VOICE_PROVIDER, provider_call_id=call_id)
        if not record:
            return

        status = "completed"
        if reason.startswith("sideband_connect_failed"):
            status = "failed"

        self._voice_calls.update_call(
            voice_call_id=record["id"],
            status=status,
            ended_at=datetime.now(timezone.utc),
            metadata_updates={"end_reason": reason},
        )

        try:
            self._agent_api.close_session(session_id=int(record["agent_session_id"]))
        except AgentApiClientError:
            pass
        self._sideband.close_call(call_id=call_id)
        logger.info("call completed", extra={"voice_call_id": record["id"], "provider_call_id": call_id, "status": status})

    def _find_existing_call(self, *, call_id: str, twilio_call_sid: str | None) -> dict[str, Any] | None:
        existing = self._voice_calls.get_by_provider_call_id(provider=VOICE_PROVIDER, provider_call_id=call_id)
        if existing:
            return existing

        if not twilio_call_sid:
            return None

        candidates = self._voice_calls.list_calls(limit=200, status=None)
        for item in candidates:
            metadata = item.get("metadata") or {}
            if item.get("provider") != VOICE_PROVIDER:
                continue
            if metadata.get("twilio_call_sid") == twilio_call_sid:
                logger.info(
                    _log_kv(
                        "voice.webhook_deduped",
                        call_id=call_id,
                        openai_call_id=call_id,
                        accepted_call_id=metadata.get("accepted_call_id"),
                        twilio_call_sid=twilio_call_sid,
                        sip_call_id=metadata.get("sip_call_id"),
                        voice_call_id=item["id"],
                        agent_session_id=item.get("agent_session_id"),
                    )
                )
                return item
        return None

    def _configured_project_id(self) -> str | None:
        _, project_id = self._sideband.credential_scope()
        return project_id or None

    def _credentials_match(self) -> bool:
        return self._realtime_client.credential_scope() == self._sideband.credential_scope()

    @staticmethod
    def _extract_event_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _extract_sip_project_id(payload: dict[str, Any]) -> str | None:
        for header in VoiceGateway._extract_sip_headers(payload):
            if str(header.get("name", "")).lower() != "to":
                continue
            raw_value = str(header.get("value", ""))
            match = re.search(r"sip:(proj_[^@;>\s]+)@", raw_value)
            if match:
                return match.group(1)
        return None

    def _handle_turn_failure(self, *, record: dict[str, Any], call_id: str, error_message: str) -> dict[str, Any]:
        self._voice_calls.update_call(
            voice_call_id=record["id"],
            status="failed",
            metadata_updates={"last_error": error_message},
        )
        try:
            self._sideband.send_text(call_id=call_id, text=FALLBACK_VOICE_ERROR_TEXT)
        except RealtimeClientError:
            pass
        logger.error("voice turn failed", extra={"voice_call_id": record["id"], "error": error_message})
        return {
            "ok": True,
            "event": "turn_failed",
            "voice_call_id": record["id"],
            "agent_session_id": record["agent_session_id"],
            "status": "failed",
            "error": "agent_unavailable",
        }

    @staticmethod
    def _extract_call_id(payload: dict[str, Any]) -> str:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        call_id = data.get("call_id")
        if not isinstance(call_id, str) or not call_id.strip():
            raise VoiceGatewayError(code="missing_call_id", message="Realtime webhook did not include call_id.", status_code=422)
        return call_id.strip()

    @staticmethod
    def _extract_caller_identifier(payload: dict[str, Any]) -> str | None:
        for header in VoiceGateway._extract_sip_headers(payload):
            header_name = str(header.get("name", "")).lower()
            if header_name not in {"from", "p-asserted-identity"}:
                continue
            raw_value = str(header.get("value", ""))
            number_match = re.search(r"\+\d{7,15}", raw_value)
            if number_match:
                return number_match.group(0)
            sip_match = re.search(r"sip:([^@>\s]+)", raw_value)
            if sip_match:
                return sip_match.group(1)
        return None

    @staticmethod
    def _extract_twilio_call_sid(payload: dict[str, Any]) -> str | None:
        for header in VoiceGateway._extract_sip_headers(payload):
            if str(header.get("name", "")).lower() == "x-twilio-callsid":
                value = str(header.get("value", "")).strip()
                return value or None
        return None

    @staticmethod
    def _extract_sip_call_id(payload: dict[str, Any]) -> str | None:
        return VoiceGateway._extract_sip_header_value(payload, "call-id")

    @staticmethod
    def _extract_sip_header_value(payload: dict[str, Any], header_name: str) -> str | None:
        normalized_name = header_name.lower()
        for header in VoiceGateway._extract_sip_headers(payload):
            if str(header.get("name", "")).lower() != normalized_name:
                continue
            value = str(header.get("value", "")).strip()
            return value or None
        return None

    @staticmethod
    def _extract_sip_headers(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        headers = data.get("sip_headers")
        if isinstance(headers, list):
            return [item for item in headers if isinstance(item, dict)]
        return []


def sanitize_for_voice(text: str) -> str:
    cleaned = text
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = cleaned.replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
