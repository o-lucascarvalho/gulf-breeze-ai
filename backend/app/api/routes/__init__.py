from app.api.routes.agent import router as agent_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.voice import router as voice_router
from app.api.routes.web import router as web_router

__all__ = ["agent_router", "dashboard_router", "health_router", "voice_router", "web_router"]
