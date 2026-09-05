from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse


REPO_ROOT = Path(__file__).resolve().parents[4]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"

router = APIRouter(include_in_schema=False)


def _frontend_index_response() -> FileResponse | HTMLResponse:
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return HTMLResponse(
        "<html><body><h1>Frontend not built</h1><p>Run the frontend build and reload this page.</p></body></html>",
        status_code=503,
    )


@router.get("/", response_model=None)
def root() -> RedirectResponse:
    return RedirectResponse(url="/chat", status_code=307)


@router.get("/chat", response_model=None)
def chat_app():
    return _frontend_index_response()


@router.get("/dashboard", response_model=None)
def dashboard_app():
    return _frontend_index_response()


@router.get("/dashboard/sessions", response_model=None)
def dashboard_sessions_app():
    return _frontend_index_response()


@router.get("/dashboard/sessions/{session_id}", response_model=None)
def dashboard_session_detail_app(session_id: str):
    return _frontend_index_response()


@router.get("/dashboard/handoffs", response_model=None)
def dashboard_handoffs_app():
    return _frontend_index_response()