from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.agent import AgentOrchestrator, build_default_agent
from app.agents.types import AgentResult, ModelResponse, ModelToolCall
from app.services.agent_actions import AgentActionService
from app.services.agent_sessions import AgentSessionService
from app.tools.registry import ToolRegistry
from app.tools.types import ToolDefinition, ToolHandledError

from .assertions import evaluate_assertions
from .types import HarnessScenario, ScenarioResult, ScenarioTurn, TurnResult


class _AnyInput(BaseModel):
    model_config = ConfigDict(extra="allow")


@dataclass
class _InMemorySessionService:
    next_id: int = 1
    records: dict[int, dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.records is None:
            self.records = {}

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


@dataclass
class _InMemoryActionService:
    actions: list[dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.actions is None:
            self.actions = []

    def log_tool_call(self, **kwargs: Any) -> None:
        self.actions.append(kwargs)


@dataclass
class _ScriptedModel:
    responses: list[ModelResponse]
    calls: int = 0

    def run(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Gulf Breeze AI evaluation scenarios")
    parser.add_argument("scenario", nargs="?", help="Scenario file path")
    parser.add_argument("--mode", choices=["fake", "real"], default="fake")
    parser.add_argument("--output", help="Write results JSON to this path")
    return parser.parse_args()


def load_scenario_file(path: str | Path) -> HarnessScenario:
    raw_path = Path(path)
    data = _load_data(raw_path.read_text(encoding="utf-8"))
    turns = [
        ScenarioTurn(
            user=turn["user"],
            script=turn.get("script", []),
            assertions=turn.get("assertions", []),
            reuse_session=turn.get("reuse_session", True),
        )
        for turn in data.get("turns", [])
    ]
    return HarnessScenario(
        name=data["name"],
        mode=data.get("mode", "fake"),
        tools=data.get("tools", []),
        turns=turns,
    )


def _load_data(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded or {}
    except Exception:
        return json.loads(text)


def _build_tool_registry(tools: list[dict[str, Any]]) -> ToolRegistry:
    definitions: list[ToolDefinition] = []
    for tool in tools:
        tool_name = str(tool["name"])
        responses = list(tool.get("responses", []))
        response = tool.get("response", {})
        error = tool.get("error")
        call_index = 0

        def handler(args: BaseModel, *, _response=response, _responses=responses, _error=error) -> dict[str, Any]:
            if _error:
                raise ToolHandledError(code=str(_error.get("code", "tool_error")), message=str(_error.get("message", "Tool failed")))
            if _responses:
                nonlocal call_index
                index = min(call_index, len(_responses) - 1)
                call_index += 1
                return _responses[index]
            return _response

        definitions.append(ToolDefinition(tool_name, str(tool.get("description", "")), _AnyInput, handler))
    return ToolRegistry(definitions)


def _build_model_response(step: dict[str, Any]) -> ModelResponse:
    tool_calls = [
        ModelToolCall(call_id=str(call.get("call_id", idx + 1)), name=str(call["name"]), arguments=call.get("arguments", {}))
        for idx, call in enumerate(step.get("tool_calls", []))
    ]
    return ModelResponse(message=step.get("message"), tool_calls=tool_calls)


def _run_fake_scenario(scenario: HarnessScenario) -> ScenarioResult:
    session_service = _InMemorySessionService()
    action_service = _InMemoryActionService()
    registry = _build_tool_registry(scenario.tools)

    turn_results: list[TurnResult] = []
    failures: list[str] = []
    previous_session_id: int | None = None

    for index, turn in enumerate(scenario.turns, start=1):
        model = _ScriptedModel([_build_model_response(step) for step in turn.script])
        agent = AgentOrchestrator(
            model=model,
            registry=registry,
            session_service=session_service,
            action_service=action_service,
            max_tool_rounds=8,
            max_history_messages=20,
        )
        result = agent.run(
            turn.user,
            session_id=previous_session_id if turn.reuse_session else None,
            channel="harness",
            finalize_session_on_return=False,
        )
        turn_payload = _serialize_result(result)
        turn_failures = evaluate_assertions(assertions=turn.assertions, result=turn_payload, previous_session_id=previous_session_id)
        if turn_failures:
            failures.extend(f"turn {index}: {item}" for item in turn_failures)
        turn_results.append(
            TurnResult(
                index=index,
                user=turn.user,
                session_id=result.session_id,
                response=result.response,
                status=result.status,
                needs_handoff=result.needs_handoff,
                handoff_reason=result.handoff_reason,
                tool_calls=[asdict(call) for call in result.tool_calls],
            )
        )
        previous_session_id = result.session_id

    return ScenarioResult(scenario=scenario.name, mode="fake", passed=not failures, failures=failures, turns=turn_results)


def _run_real_scenario(scenario: HarnessScenario) -> ScenarioResult:
    agent = build_default_agent()
    turn_results: list[TurnResult] = []
    failures: list[str] = []
    previous_session_id: int | None = None

    for index, turn in enumerate(scenario.turns, start=1):
        result = agent.run(
            turn.user,
            session_id=previous_session_id if turn.reuse_session else None,
            channel="harness",
            finalize_session_on_return=False,
        )
        turn_payload = _serialize_result(result)
        turn_failures = evaluate_assertions(assertions=turn.assertions, result=turn_payload, previous_session_id=previous_session_id)
        if turn_failures:
            failures.extend(f"turn {index}: {item}" for item in turn_failures)
        turn_results.append(
            TurnResult(
                index=index,
                user=turn.user,
                session_id=result.session_id,
                response=result.response,
                status=result.status,
                needs_handoff=result.needs_handoff,
                handoff_reason=result.handoff_reason,
                tool_calls=[asdict(call) for call in result.tool_calls],
            )
        )
        previous_session_id = result.session_id

    return ScenarioResult(scenario=scenario.name, mode="real", passed=not failures, failures=failures, turns=turn_results)


def _serialize_result(result: AgentResult) -> dict[str, Any]:
    return {
        "session_id": result.session_id,
        "response": result.response,
        "status": result.status,
        "needs_handoff": result.needs_handoff,
        "handoff_reason": result.handoff_reason,
        "tool_calls": [asdict(call) for call in result.tool_calls],
        "active_customer_id": result.active_customer_id,
        "active_job_id": result.active_job_id,
        "active_invoice_id": result.active_invoice_id,
        "referenced_note_ids": result.referenced_note_ids,
    }


def run_scenario(scenario: HarnessScenario, *, mode: str | None = None) -> ScenarioResult:
    effective_mode = mode or scenario.mode
    if effective_mode == "real":
        return _run_real_scenario(scenario)
    return _run_fake_scenario(scenario)


def write_result(result: ScenarioResult, path: str | Path) -> None:
    payload = {
        "scenario": result.scenario,
        "mode": result.mode,
        "passed": result.passed,
        "failures": result.failures,
        "turns": [asdict(turn) for turn in result.turns],
    }
    Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not args.scenario:
        raise SystemExit("A scenario file path is required.")

    scenario = load_scenario_file(args.scenario)
    result = run_scenario(scenario, mode=args.mode)
    if args.output:
        write_result(result, args.output)

    print(json.dumps({"scenario": result.scenario, "mode": result.mode, "passed": result.passed, "failures": result.failures}, indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
