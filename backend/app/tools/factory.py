from __future__ import annotations

from app.tools.customers import build_customer_tools
from app.tools.handoff import build_handoff_tool
from app.tools.invoices import build_invoice_tools
from app.tools.jobs import build_job_tools
from app.tools.notes import build_note_tools
from app.tools.registry import ToolRegistry
from app.tools.scheduling import build_schedule_tools
from app.tools.weather import build_weather_tools
from app.tools.semantic import build_semantic_tool


def build_default_tool_registry(session_factory, semantic_retriever, *, weather_service=None) -> ToolRegistry:
    tools = []
    tools.extend(build_customer_tools(session_factory))
    tools.extend(build_job_tools(session_factory))
    tools.extend(build_schedule_tools(session_factory))
    tools.extend(build_weather_tools(session_factory, weather_service=weather_service))
    tools.extend(build_invoice_tools(session_factory))
    tools.extend(build_note_tools(session_factory))
    tools.append(build_semantic_tool(semantic_retriever))
    tools.append(build_handoff_tool())
    return ToolRegistry(tools)
