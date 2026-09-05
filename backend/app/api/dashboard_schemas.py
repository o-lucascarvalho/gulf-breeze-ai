from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class PublicConfigResponse(BaseModel):
    public_phone_enabled: bool
    public_phone_number: str | None = None


class DashboardSessionSummaryResponse(BaseModel):
    session_id: int
    started_at: datetime
    ended_at: datetime | None = None
    channel: str
    status: str
    customer_id: str | None = None
    customer_name: str | None = None
    summary: str
    message_count: int
    action_count: int
    needs_handoff: bool
    handoff_reason: str | None = None


class DashboardAppointmentResponse(BaseModel):
    job_id: str
    customer_name: str | None = None
    description: str | None = None
    address: str | None = None
    technicians: list[str] = Field(default_factory=list)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    effective_start_at: datetime | None = None
    effective_end_at: datetime | None = None
    original_start_at: datetime | None = None
    original_end_at: datetime | None = None
    status: str | None = None
    has_schedule_override: bool = False
    contact_summary: str | None = None
    weather: dict[str, Any] | None = None


class DashboardOutstandingAccountResponse(BaseModel):
    invoice_id: str
    invoice_number: str | None = None
    customer_name: str | None = None
    job_description: str | None = None
    status: str | None = None
    due_amount: float
    service_date: date | None = None


class DashboardSummaryResponse(BaseModel):
    customers: int
    jobs: int
    ai_conversations: int
    active_sessions: int
    human_handoffs: int
    upcoming_appointments: int
    open_invoices: int
    outstanding_balance: float
    recent_sessions: list[DashboardSessionSummaryResponse] = Field(default_factory=list)
    appointments: list[DashboardAppointmentResponse] = Field(default_factory=list)
    recent_schedule_changes: list["ScheduleChangeResponse"] = Field(default_factory=list)
    outstanding_accounts: list[DashboardOutstandingAccountResponse] = Field(default_factory=list)


class DashboardConversationMessageResponse(BaseModel):
    role: str
    content: str
    tool_call_id: str | None = None


class DashboardActionResponse(BaseModel):
    id: int
    timestamp: datetime
    action_type: str
    display_label: str
    detail: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None


class DashboardSessionDetailResponse(BaseModel):
    session: DashboardSessionSummaryResponse
    conversation: list[DashboardConversationMessageResponse] = Field(default_factory=list)
    actions: list[DashboardActionResponse] = Field(default_factory=list)


class AppointmentListResponse(BaseModel):
    items: list[DashboardAppointmentResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class ScheduleChangeResponse(BaseModel):
    change_id: int
    job_id: str
    customer_name: str | None = None
    address: str | None = None
    description: str | None = None
    original_start_at: datetime | None = None
    original_end_at: datetime | None = None
    new_start_at: datetime | None = None
    new_end_at: datetime | None = None
    reason: str | None = None
    status: str
    created_at: datetime
    agent_session_id: int | None = None
    channel: str | None = None
    changed_by: str


class ScheduleChangeListResponse(BaseModel):
    items: list[ScheduleChangeResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


DashboardSummaryResponse.model_rebuild()