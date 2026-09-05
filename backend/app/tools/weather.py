from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.repositories.job_repository import JobRepository
from app.services.schedule_overrides import ScheduleOverrideService
from app.services.weather import WeatherService
from app.tools.helpers import to_jsonable
from app.tools.types import ToolDefinition, ToolHandledError


class GetWeatherForecastInput(BaseModel):
    job_id: str
    forecast_at: datetime | None = None


def build_weather_tools(session_factory, *, weather_service: WeatherService | None = None) -> list[ToolDefinition]:
    repository = JobRepository()
    schedule_service = ScheduleOverrideService()
    weather_service = weather_service or WeatherService()

    def get_weather_forecast(args: GetWeatherForecastInput) -> dict:
        with session_factory() as session:
            job = repository.get_job(session, args.job_id)
            if job is None:
                raise ToolHandledError(code="job_not_found", message="The specified job was not found.")

            effective = schedule_service.resolve_for_jobs(session, [job]).get(job.id)
            forecast_at = args.forecast_at or (effective.effective_start_at if effective else job.scheduled_start)
            if forecast_at is not None:
                forecast_at = ScheduleOverrideService.coerce_miami_wall_time(forecast_at)
            report = weather_service.build_forecast_report(job, forecast_at)

        return to_jsonable(report)

    return [
        ToolDefinition(
            name="get_weather_forecast",
            description=(
                "Get a concise weather forecast for a job appointment using the effective appointment time by default. "
                "Treat optional forecast_at values as Miami local wall-clock times. "
                "Use after rescheduling or when the user asks about weather for an existing appointment."
            ),
            input_model=GetWeatherForecastInput,
            handler=get_weather_forecast,
        )
    ]
