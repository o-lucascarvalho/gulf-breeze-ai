from __future__ import annotations

from functools import lru_cache

from app.services.agent_service import AgentService, build_default_agent_service
from app.core.config import get_settings
from app.services.agent_api_client import AgentApiClient
from app.services.dashboard import DashboardService
from app.services.openai_realtime import OpenAIRealtimeClient, OpenAIRealtimeSidebandManager
from app.services.weather import WeatherService
from app.services.voice_calls import VoiceCallService
from app.services.voice_gateway import VOICE_INSTRUCTIONS, VoiceGateway


def _parse_sideband_retry_delays(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    for part in raw.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        delay_ms = int(stripped)
        if delay_ms < 0:
            raise ValueError("VOICE_SIDEBAND_RETRY_DELAYS_MS must contain only non-negative integers")
        values.append(delay_ms)
    return tuple(values)


@lru_cache(maxsize=1)
def get_agent_service() -> AgentService:
    return build_default_agent_service()


@lru_cache(maxsize=1)
def get_dashboard_service() -> DashboardService:
    settings = get_settings()
    return DashboardService(
        weather_service=WeatherService(
            timeout_seconds=settings.weather_timeout_seconds,
            cache_ttl_seconds=settings.weather_cache_ttl_seconds,
        )
    )


@lru_cache(maxsize=1)
def get_voice_gateway() -> VoiceGateway:
    settings = get_settings()
    return VoiceGateway(
        enabled=settings.voice_agent_enabled,
        greeting_enabled=settings.voice_greeting_enabled,
        greeting_text=settings.voice_greeting_text,
        voice_calls=VoiceCallService(),
        agent_api=AgentApiClient(
            base_url=settings.voice_agent_api_base_url,
            timeout_seconds=settings.voice_agent_api_timeout_seconds,
        ),
        realtime_client=OpenAIRealtimeClient(
            api_key=settings.openai_api_key,
            project_id=settings.openai_project_id,
            model=settings.openai_realtime_model,
            voice=settings.openai_realtime_voice,
            timeout_seconds=settings.voice_realtime_timeout_seconds,
        ),
        sideband=OpenAIRealtimeSidebandManager(
            api_key=settings.openai_api_key,
            project_id=settings.openai_project_id,
            model=settings.openai_realtime_model,
            voice=settings.openai_realtime_voice,
            instructions=VOICE_INSTRUCTIONS,
            retry_delays_ms=_parse_sideband_retry_delays(settings.voice_sideband_retry_delays_ms),
        ),
    )