from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass(frozen=True)
class AgentApiClientError(Exception):
    code: str
    message: str
    status_code: int


class AgentApiClient:
    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def create_session(self, *, caller_identifier: str | None = None) -> dict[str, Any]:
        payload = {}
        if caller_identifier:
            payload = {"caller_identifier": caller_identifier}
        return self._request_json("POST", "/agent/sessions", payload)

    def chat(self, *, message: str, session_id: int) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/agent/chat",
            {
                "message": message,
                "session_id": session_id,
                "debug": False,
            },
        )

    def close_session(self, *, session_id: int) -> dict[str, Any]:
        return self._request_json("POST", f"/agent/sessions/{session_id}/close", {})

    def _request_json(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = request.Request(
            f"{self._base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )

        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8") if exc.fp else ""
            detail = "Agent API request failed"
            code = "agent_api_http_error"
            if raw:
                try:
                    payload = json.loads(raw)
                    detail = payload.get("error", {}).get("message", detail)
                    code = payload.get("error", {}).get("code", code)
                except json.JSONDecodeError:
                    detail = raw
            raise AgentApiClientError(code=code, message=detail, status_code=exc.code) from exc
        except TimeoutError as exc:
            raise AgentApiClientError(
                code="agent_api_timeout",
                message="Timed out while contacting the Agent API.",
                status_code=504,
            ) from exc
        except Exception as exc:
            raise AgentApiClientError(
                code="agent_api_unavailable",
                message="Could not reach the Agent API.",
                status_code=503,
            ) from exc
