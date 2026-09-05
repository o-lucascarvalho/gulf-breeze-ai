from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
from urllib import error, request

from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as ws_connect


@dataclass(frozen=True)
class RealtimeClientError(Exception):
    code: str
    message: str
    status_code: int


class OpenAIRealtimeClient:
    def __init__(
        self,
        *,
        api_key: str,
        project_id: str,
        model: str,
        voice: str,
        timeout_seconds: float,
    ) -> None:
        self._api_key = api_key
        self._project_id = project_id
        self._model = model
        self._voice = voice
        self._timeout_seconds = timeout_seconds

    def credential_scope(self) -> tuple[str, str]:
        return self._api_key, self._project_id

    def accept_call(self, *, call_id: str, instructions: str, caller_identifier: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "voice": self._voice,
            "instructions": instructions,
        }
        if caller_identifier:
            payload["metadata"] = {"caller_identifier": caller_identifier}
        return self._request_json("POST", f"/v1/realtime/calls/{call_id}/accept", payload)

    def speak_text(self, *, call_id: str, text: str) -> dict[str, Any]:
        payload = {
            "type": "response.create",
            "response": {
                "modalities": ["audio", "text"],
                "voice": self._voice,
                "instructions": text,
            },
        }
        return self._request_json("POST", f"/v1/realtime/calls/{call_id}/events", payload)

    def _request_json(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._project_id:
            headers["OpenAI-Project"] = self._project_id

        req = request.Request(
            f"https://api.openai.com{path}",
            data=data,
            method=method,
            headers=headers,
        )

        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            detail = "OpenAI Realtime request failed"
            raw = exc.read().decode("utf-8") if exc.fp else ""
            if raw:
                try:
                    payload = json.loads(raw)
                    detail = payload.get("error", {}).get("message", detail)
                except json.JSONDecodeError:
                    detail = raw
            raise RealtimeClientError(code="realtime_http_error", message=detail, status_code=exc.code) from exc
        except TimeoutError as exc:
            raise RealtimeClientError(
                code="realtime_timeout",
                message="Timed out while contacting OpenAI Realtime.",
                status_code=504,
            ) from exc
        except Exception as exc:
            raise RealtimeClientError(
                code="realtime_unavailable",
                message="Could not reach OpenAI Realtime.",
                status_code=503,
            ) from exc


logger = logging.getLogger("uvicorn.error")


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


def datetime_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(start: float | None, end: float) -> int | None:
    if start is None:
        return None
    return int((end - start) * 1000)


class OpenAIRealtimeSidebandManager:
    def __init__(
        self,
        *,
        api_key: str,
        project_id: str,
        model: str,
        voice: str,
        instructions: str,
        websocket_base_url: str = "https://api.openai.com/v1",
        retry_delays_ms: tuple[int, ...] = (200, 400, 800, 1200),
        _connect=ws_connect,
        _sleep=time.sleep,
    ) -> None:
        self._api_key = api_key
        self._project_id = project_id
        self._model = model
        self._voice = voice
        self._instructions = instructions
        self._websocket_base_url = websocket_base_url
        self._retry_delays_ms = retry_delays_ms
        self._connect = _connect
        self._sleep = _sleep
        self._lock = threading.Lock()
        self._connections: dict[str, Any] = {}
        self._threads: dict[str, threading.Thread] = {}

    def credential_scope(self) -> tuple[str, str]:
        return self._api_key, self._project_id

    def describe_connection_target(self, *, call_id: str) -> dict[str, Any]:
        return {
            "call_id": call_id,
            "url": self._build_realtime_url(call_id=call_id),
            "project_id": self._project_id or None,
            "project_header_present": bool(self._project_id),
        }

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
        with self._lock:
            if call_id in self._threads:
                return

        thread = threading.Thread(
            target=self._run_call_loop,
            kwargs={
                "call_id": call_id,
                "accepted_at_utc": accepted_at_utc,
                "accepted_at_monotonic": accepted_at_monotonic,
                "on_connected": on_connected,
                "on_completed_transcript": on_completed_transcript,
                "on_disconnected": on_disconnected,
            },
            daemon=True,
            name=f"realtime-sideband-{call_id}",
        )
        with self._lock:
            self._threads[call_id] = thread
        thread.start()

    def send_text(self, *, call_id: str, text: str) -> None:
        with self._lock:
            connection = self._connections.get(call_id)
        if connection is None:
            raise RealtimeClientError(
                code="sideband_not_connected",
                message="Realtime sideband connection is not active for this call.",
                status_code=409,
            )

        try:
            connection.send(
                json.dumps(
                    {
                        "type": "response.create",
                        "response": {
                            "output_modalities": ["audio"],
                            "instructions": f"Read this to the caller verbatim: {text}",
                        },
                    }
                )
            )
        except Exception as exc:
            raise RealtimeClientError(
                code="sideband_send_failed",
                message="Failed to send response text over Realtime sideband.",
                status_code=502,
            ) from exc

    def close_call(self, *, call_id: str) -> None:
        with self._lock:
            connection = self._connections.pop(call_id, None)
            self._threads.pop(call_id, None)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def _run_call_loop(
        self,
        *,
        call_id: str,
        accepted_at_utc: str | None = None,
        accepted_at_monotonic: float | None = None,
        on_connected,
        on_completed_transcript,
        on_disconnected,
    ) -> None:
        disconnected_reason = "sideband_closed"
        connection = None
        target = self.describe_connection_target(call_id=call_id)
        max_attempts = len(self._retry_delays_ms) + 1
        try:
            headers = {"Authorization": f"Bearer {self._api_key}"}
            if self._project_id:
                headers["OpenAI-Project"] = self._project_id

            for attempt in range(1, max_attempts + 1):
                attempt_monotonic = time.perf_counter()
                logger.info(
                    _log_kv(
                        "voice.sideband_connect_attempt",
                        utc_ts=datetime_now_iso(),
                        accepted_at_utc=accepted_at_utc,
                        elapsed_since_accept_ms=_elapsed_ms(accepted_at_monotonic, attempt_monotonic),
                        call_id=call_id,
                        attempt=attempt,
                        max_attempts=max_attempts,
                    )
                )
                try:
                    connection = self._connect(target["url"], additional_headers=headers)
                    logger.info(
                        _log_kv(
                            "voice.sideband_connected",
                            utc_ts=datetime_now_iso(),
                            accepted_at_utc=accepted_at_utc,
                            elapsed_since_accept_ms=_elapsed_ms(accepted_at_monotonic, time.perf_counter()),
                            call_id=call_id,
                            attempt=attempt,
                        )
                    )
                    break
                except InvalidStatus as exc:
                    diagnostics = self._invalid_status_diagnostics(exc)
                    retry_delay_ms = self._retry_delay_for(
                        attempt=attempt,
                        max_attempts=max_attempts,
                        diagnostics=diagnostics,
                    )
                    if retry_delay_ms is None:
                        disconnected_reason = self._disconnected_reason_for(diagnostics=diagnostics)
                        logger.exception(
                            _log_kv(
                                "voice.sideband_connect_failed",
                                utc_ts=datetime_now_iso(),
                                accepted_at_utc=accepted_at_utc,
                                elapsed_since_accept_ms=_elapsed_ms(accepted_at_monotonic, time.perf_counter()),
                                call_id=call_id,
                                url=target["url"],
                                project_id=target["project_id"],
                                project_header_present=target["project_header_present"],
                                http_status=diagnostics["http_status"],
                                http_reason=diagnostics["http_reason"],
                                classification=diagnostics["classification"],
                                openai_request_id=diagnostics["openai_request_id"],
                                provider_error_code=diagnostics["provider_error_code"],
                                response_body=diagnostics["response_body"],
                                attempts=attempt,
                            )
                        )
                        raise

                    logger.info(
                        _log_kv(
                            "voice.sideband_call_not_ready",
                            utc_ts=datetime_now_iso(),
                            accepted_at_utc=accepted_at_utc,
                            elapsed_since_accept_ms=_elapsed_ms(accepted_at_monotonic, time.perf_counter()),
                            call_id=call_id,
                            attempt=attempt,
                            retry_delay_ms=retry_delay_ms,
                        )
                    )
                    self._sleep(retry_delay_ms / 1000.0)
            else:
                raise AssertionError("sideband retry loop terminated without connection or exception")

            with self._lock:
                self._connections[call_id] = connection

            on_connected(call_id)

            connection.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "instructions": self._instructions,
                        },
                    }
                )
            )
            while True:
                raw = connection.recv()
                if raw is None:
                    break
                event = json.loads(raw)
                event_type = str(event.get("type", ""))
                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = str(event.get("transcript", "") or "").strip()
                    if transcript:
                        on_completed_transcript(call_id, transcript)
                elif event_type == "error":
                    disconnected_reason = "sideband_error_event"
        except InvalidStatus as exc:
            diagnostics = self._invalid_status_diagnostics(exc)
            disconnected_reason = self._disconnected_reason_for(diagnostics=diagnostics)
        except Exception as exc:
            disconnected_reason = f"sideband_connect_failed:{type(exc).__name__}"
            logger.exception(
                _log_kv(
                    "voice.sideband_connect_failed",
                    utc_ts=datetime_now_iso(),
                    accepted_at_utc=accepted_at_utc,
                    elapsed_since_accept_ms=_elapsed_ms(accepted_at_monotonic, time.perf_counter()),
                    call_id=call_id,
                    url=target["url"],
                    project_id=target["project_id"],
                    project_header_present=target["project_header_present"],
                    error=str(exc),
                )
            )
        finally:
            self.close_call(call_id=call_id)
            on_disconnected(call_id, disconnected_reason)

    def _build_realtime_url(self, *, call_id: str) -> str:
        parsed = urlparse(self._websocket_base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        base_path = parsed.path.rstrip("/")
        if base_path.endswith("/realtime"):
            realtime_path = base_path
        else:
            realtime_path = f"{base_path}/realtime"
        return urlunparse(
            (
                scheme,
                parsed.netloc,
                realtime_path,
                "",
                urlencode({"call_id": call_id}),
                "",
            )
        )

    @staticmethod
    def _invalid_status_diagnostics(exc: InvalidStatus) -> dict[str, Any]:
        response = exc.response
        body_bytes = bytes(getattr(response, "body", b"") or b"")
        body_text = body_bytes.decode("utf-8", errors="replace").strip()
        safe_body = body_text[:500]
        http_status = int(getattr(response, "status_code", 0) or 0)
        safe_headers = OpenAIRealtimeSidebandManager._safe_response_headers(getattr(response, "headers", {}))
        openai_request_id = OpenAIRealtimeSidebandManager._header_value(safe_headers, "x-request-id")
        provider_error_code = None
        provider_error_type = None
        provider_error_message = None
        try:
            payload = json.loads(body_text) if body_text else {}
            if isinstance(payload, dict):
                error_payload = payload.get("error")
                if isinstance(error_payload, dict):
                    provider_error_code = error_payload.get("code")
                    provider_error_type = error_payload.get("type")
                    provider_error_message = error_payload.get("message")
        except json.JSONDecodeError:
            pass
        return {
            "http_status": http_status,
            "http_reason": str(getattr(response, "reason_phrase", "") or ""),
            "response_headers": safe_headers,
            "response_body": safe_body,
            "openai_request_id": openai_request_id,
            "provider_error_code": provider_error_code,
            "provider_error_type": provider_error_type,
            "provider_error_message": provider_error_message,
            "classification": OpenAIRealtimeSidebandManager._classify_invalid_status(
                http_status=http_status,
                body_text=body_text,
                headers=safe_headers,
                provider_error_code=provider_error_code,
            ),
        }

    @staticmethod
    def _safe_response_headers(headers: Any) -> dict[str, str]:
        redacted_names = {"authorization", "cookie", "set-cookie", "proxy-authorization"}
        safe_headers: dict[str, str] = {}
        if hasattr(headers, "raw_items"):
            items = list(headers.raw_items())
        elif hasattr(headers, "items"):
            items = list(headers.items())
        else:
            items = []

        for key, value in items:
            key_str = str(key)
            if key_str.lower() in redacted_names:
                continue
            safe_headers[key_str] = str(value)
        return safe_headers

    @staticmethod
    def _header_value(headers: dict[str, str], target_name: str) -> str | None:
        normalized_target = target_name.lower()
        for key, value in headers.items():
            if key.lower() == normalized_target:
                return value
        return None

    @staticmethod
    def _classify_invalid_status(
        *, http_status: int, body_text: str, headers: dict[str, str], provider_error_code: str | None
    ) -> str:
        lowered_body = body_text.lower()
        lowered_headers = {key.lower(): value.lower() for key, value in headers.items()}
        if http_status == 404:
            if provider_error_code == "call_id_not_found":
                return "call_id_not_found"
            if "call" in lowered_body and "not found" in lowered_body:
                return "call_not_found"
            if "project" in lowered_body and "mismatch" in lowered_body:
                return "project_mismatch"
            if "expired" in lowered_body:
                return "expired_call"
            if any("not found" in value for value in lowered_headers.values()):
                return "not_found"
            return "invalid_endpoint_or_call_not_found"
        if http_status in {401, 403}:
            return "project_or_auth_mismatch"
        if http_status == 410:
            return "expired_call"
        return "unknown"

    def _retry_delay_for(self, *, attempt: int, max_attempts: int, diagnostics: dict[str, Any]) -> int | None:
        if diagnostics.get("http_status") != 404:
            return None
        if diagnostics.get("provider_error_code") != "call_id_not_found":
            return None
        if attempt >= max_attempts:
            return None
        return self._retry_delays_ms[attempt - 1]

    @staticmethod
    def _disconnected_reason_for(*, diagnostics: dict[str, Any]) -> str:
        return (
            f"sideband_connect_failed:http_{diagnostics['http_status']}:"
            f"{diagnostics['classification']}"
        )
