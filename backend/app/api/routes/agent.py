from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_agent_service
from app.api.schemas import (
    AgentActionResponse,
    AgentChatRequest,
    AgentChatResponse,
    AgentSessionCreateRequest,
    AgentSessionCloseResponse,
    AgentSessionResponse,
)
from app.services.agent_service import AgentService, AgentServiceError

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=AgentChatResponse, response_model_exclude_none=True)
def chat(request: AgentChatRequest, service: AgentService = Depends(get_agent_service)) -> dict:
    if not request.message or not request.message.strip():
        raise AgentServiceError(
            code="invalid_message",
            message="Message cannot be empty.",
            status_code=422,
        )

    return service.chat(
        message=request.message,
        session_id=request.session_id,
        caller_identifier=None,
        debug=request.debug,
    )


@router.post("/sessions", response_model=AgentSessionResponse, response_model_exclude_none=True)
def create_session(
    request: AgentSessionCreateRequest | None = None,
    service: AgentService = Depends(get_agent_service),
) -> dict:
    caller_identifier = request.caller_identifier if request else None
    channel = request.channel if request and request.channel else "api"
    return service.create_session(caller_identifier=caller_identifier, channel=channel)


@router.post("/sessions/{session_id}/close", response_model=AgentSessionCloseResponse)
def close_session(session_id: int, service: AgentService = Depends(get_agent_service)) -> dict:
    return service.close_session(session_id)


@router.get("/sessions/{session_id}", response_model=AgentSessionResponse, response_model_exclude_none=True)
def get_session(session_id: int, service: AgentService = Depends(get_agent_service)) -> dict:
    return service.get_session(session_id)


@router.get("/sessions/{session_id}/actions", response_model=list[AgentActionResponse], response_model_exclude_none=True)
def get_session_actions(
    session_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    service: AgentService = Depends(get_agent_service),
) -> list[dict]:
    return service.list_actions(session_id=session_id, limit=limit)


@router.get("/sessions", response_model=list[AgentSessionResponse], response_model_exclude_none=True)
def list_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    service: AgentService = Depends(get_agent_service),
) -> list[dict]:
    return service.list_sessions(limit=limit, status=status)
