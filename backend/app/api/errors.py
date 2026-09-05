from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.agent_service import AgentServiceError
from app.services.dashboard import DashboardServiceError
from app.services.voice_gateway import VoiceGatewayError


def error_response(*, code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


async def agent_service_error_handler(request: Request, exc: AgentServiceError) -> JSONResponse:
    return error_response(code=exc.code, message=exc.message, status_code=exc.status_code)


async def dashboard_service_error_handler(request: Request, exc: DashboardServiceError) -> JSONResponse:
    return error_response(code=exc.code, message=exc.message, status_code=exc.status_code)


async def voice_gateway_error_handler(request: Request, exc: VoiceGatewayError) -> JSONResponse:
    return error_response(code=exc.code, message=exc.message, status_code=exc.status_code)


async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    message = "Invalid request."
    for error in exc.errors():
        loc = error.get("loc", [])
        if loc and loc[0] == "body" and "message" in loc:
            message = "Message cannot be empty."
            return error_response(code="invalid_message", message=message, status_code=422)
    return error_response(code="invalid_request", message=message, status_code=422)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(code="http_error", message=str(exc.detail), status_code=exc.status_code)


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(
        code="internal_error",
        message="An internal error occurred while processing the request.",
        status_code=500,
    )