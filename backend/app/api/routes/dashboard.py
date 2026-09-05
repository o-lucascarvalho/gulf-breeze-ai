from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.api.dashboard_schemas import (
    AppointmentListResponse,
    DashboardSessionDetailResponse,
    DashboardSessionSummaryResponse,
    ScheduleChangeListResponse,
    DashboardSummaryResponse,
    PublicConfigResponse,
)
from app.api.dependencies import get_dashboard_service
from app.core.config import get_settings
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/public/config", response_model=PublicConfigResponse)
def get_public_config() -> dict:
    settings = get_settings()
    return {
        "public_phone_enabled": settings.public_phone_enabled,
        "public_phone_number": settings.public_phone_number if settings.public_phone_enabled else None,
    }


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(service: DashboardService = Depends(get_dashboard_service)) -> dict:
    return service.get_summary()


@router.get("/dashboard/sessions", response_model=list[DashboardSessionSummaryResponse])
def list_dashboard_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    service: DashboardService = Depends(get_dashboard_service),
) -> list[dict]:
    return service.list_sessions(limit=limit)


@router.get("/dashboard/sessions/{session_id}", response_model=DashboardSessionDetailResponse)
def get_dashboard_session(session_id: int, service: DashboardService = Depends(get_dashboard_service)) -> dict:
    return service.get_session_detail(session_id)


@router.get("/dashboard/handoffs", response_model=list[DashboardSessionSummaryResponse])
def list_dashboard_handoffs(
    limit: int = Query(default=50, ge=1, le=200),
    service: DashboardService = Depends(get_dashboard_service),
) -> list[dict]:
    return service.list_handoffs(limit=limit)


@router.get("/dashboard/appointments", response_model=AppointmentListResponse)
def list_dashboard_appointments(
    q: str | None = Query(default=None),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.list_appointments(
        q=q,
        from_at=from_at,
        to_at=to_at,
        limit=limit,
        offset=offset,
    )


@router.get("/dashboard/schedule-changes", response_model=ScheduleChangeListResponse)
def list_dashboard_schedule_changes(
    q: str | None = Query(default=None),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: DashboardService = Depends(get_dashboard_service),
) -> dict:
    return service.list_schedule_changes(
        q=q,
        from_at=from_at,
        to_at=to_at,
        limit=limit,
        offset=offset,
    )