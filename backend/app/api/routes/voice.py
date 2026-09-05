from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_voice_gateway
from app.services.voice_gateway import VoiceGatewayError
from app.services.voice_gateway import VoiceGateway

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/openai/webhook")
def openai_webhook(payload: dict[str, Any], gateway: VoiceGateway = Depends(get_voice_gateway)) -> dict[str, Any]:
    return gateway.handle_openai_webhook(payload)


@router.get("/calls/{voice_call_id}")
def get_call(voice_call_id: int, gateway: VoiceGateway = Depends(get_voice_gateway)) -> dict[str, Any]:
    record = gateway.get_call(voice_call_id)
    if not record:
        raise VoiceGatewayError(code="call_not_found", message="Voice call was not found.", status_code=404)
    return record


@router.get("/calls")
def list_calls(
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    gateway: VoiceGateway = Depends(get_voice_gateway),
) -> list[dict[str, Any]]:
    return gateway.list_calls(limit=limit, status=status)
