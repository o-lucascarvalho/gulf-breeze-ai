from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import (
	agent_service_error_handler,
	dashboard_service_error_handler,
	http_exception_handler,
	internal_error_handler,
	request_validation_error_handler,
	voice_gateway_error_handler,
)
from app.api.routes import agent_router, dashboard_router, health_router, voice_router, web_router
from app.services.agent_service import AgentServiceError
from app.services.dashboard import DashboardServiceError
from app.services.voice_gateway import VoiceGatewayError
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.add_exception_handler(AgentServiceError, agent_service_error_handler)
app.add_exception_handler(DashboardServiceError, dashboard_service_error_handler)
app.add_exception_handler(VoiceGatewayError, voice_gateway_error_handler)
app.add_exception_handler(RequestValidationError, request_validation_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, internal_error_handler)
app.include_router(health_router)
app.include_router(agent_router)
app.include_router(dashboard_router)
app.include_router(voice_router)

frontend_assets_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "assets"
if frontend_assets_dir.exists():
	app.mount("/assets", StaticFiles(directory=frontend_assets_dir), name="frontend-assets")

app.include_router(web_router)
