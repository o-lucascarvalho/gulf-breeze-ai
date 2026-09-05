from __future__ import annotations


class AgentError(RuntimeError):
    """Base error for agent orchestration."""


class AgentMaxRoundsExceeded(AgentError):
    """Raised when tool-call rounds exceed configured bound."""
