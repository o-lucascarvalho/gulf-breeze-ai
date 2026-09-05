from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScenarioTurn:
    user: str
    script: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    reuse_session: bool = True


@dataclass(frozen=True)
class HarnessScenario:
    name: str
    mode: str = "fake"
    tools: list[dict[str, Any]] = field(default_factory=list)
    turns: list[ScenarioTurn] = field(default_factory=list)


@dataclass(frozen=True)
class TurnResult:
    index: int
    user: str
    session_id: int
    response: str
    status: str
    needs_handoff: bool
    handoff_reason: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    mode: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    turns: list[TurnResult] = field(default_factory=list)
