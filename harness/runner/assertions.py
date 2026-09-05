from __future__ import annotations

from typing import Any


def evaluate_assertions(*, assertions: list[dict[str, Any]], result: dict[str, Any], previous_session_id: int | None) -> list[str]:
    failures: list[str] = []
    for assertion in assertions:
        assertion_type = assertion.get("type")
        if assertion_type == "response_contains":
            needle = str(assertion.get("value", ""))
            if needle not in result.get("response", ""):
                failures.append(f'response did not contain {needle!r}')
        elif assertion_type == "response_not_contains":
            needle = str(assertion.get("value", ""))
            if needle in result.get("response", ""):
                failures.append(f'response unexpectedly contained {needle!r}')
        elif assertion_type == "response_equals":
            expected = str(assertion.get("value", ""))
            if result.get("response") != expected:
                failures.append(f'response did not equal {expected!r}')
        elif assertion_type == "status_equals":
            expected = str(assertion.get("value", ""))
            if result.get("status") != expected:
                failures.append(f'status was {result.get("status")!r}, expected {expected!r}')
        elif assertion_type == "needs_handoff":
            expected = bool(assertion.get("value", True))
            if bool(result.get("needs_handoff")) != expected:
                failures.append(f'needs_handoff was {result.get("needs_handoff")!r}, expected {expected!r}')
        elif assertion_type == "tool_sequence":
            expected = list(assertion.get("value", []))
            actual = [call.get("tool_name") for call in result.get("tool_calls", [])]
            if actual != expected:
                failures.append(f'tool sequence was {actual!r}, expected {expected!r}')
        elif assertion_type == "session_reused":
            expected = bool(assertion.get("value", True))
            if expected and previous_session_id is not None and result.get("session_id") != previous_session_id:
                failures.append(f'session_id {result.get("session_id")!r} did not reuse {previous_session_id!r}')
        elif assertion_type == "session_new":
            if previous_session_id is not None and result.get("session_id") == previous_session_id:
                failures.append(f'session_id {result.get("session_id")!r} was expected to change')
        elif assertion_type == "field_equals":
            field_name = str(assertion.get("field"))
            expected = assertion.get("value")
            if result.get(field_name) != expected:
                failures.append(f'field {field_name!r} was {result.get(field_name)!r}, expected {expected!r}')
        else:
            failures.append(f'unknown assertion type {assertion_type!r}')
    return failures
