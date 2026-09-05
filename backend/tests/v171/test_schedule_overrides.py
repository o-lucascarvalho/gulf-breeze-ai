from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.agents.agent import AgentOrchestrator
from app.agents.types import ModelResponse, ModelToolCall
from app.db.base import Base
from app.db.models import (
    AgentAction,
    AgentSession,
    Customer,
    Employee,
    Job,
    JobEmployee,
    JobScheduleOverride,
)
from app.services.agent_actions import AgentActionService
from app.services.agent_sessions import AgentSessionService
from app.services.dashboard import DashboardService
from app.services.schedule_overrides import ScheduleOverrideService
from app.services.weather import MIAMI_TZ, WeatherService
from app.tools.factory import build_default_tool_registry


MIAMI = ZoneInfo("America/New_York")


def miami_dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=MIAMI)


def utc_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint_sqlite(_type, _compiler, **_kwargs):
    return "INTEGER"


@dataclass
class FakeSemanticRetriever:
    def search(self, query: str, *, top_k: int = 10, customer_id: str | None = None, job_id: str | None = None):
        return []


@dataclass
class FakeWeatherService:
    calls: int = 0

    def get_weather_for_appointment(self, job: Job, effective_start_at: datetime | None) -> dict[str, Any] | None:
        self.calls += 1
        return {
            "temperature_f": 87,
            "precipitation_probability": 20,
            "wind_mph": 11,
            "condition": "Partly cloudy",
            "forecast_at": (effective_start_at or datetime.now(timezone.utc)).isoformat(),
            "location_precision": "zip",
            "risk_indicator": None,
        }


class FailingWeatherService:
    def get_weather_for_appointment(self, job: Job, effective_start_at: datetime | None) -> dict[str, Any] | None:
        raise RuntimeError("weather provider timeout")


@dataclass
class ControlledWeatherHttp:
    geocode_payload: dict[str, Any] | None = None
    forecast_payload: dict[str, Any] | None = None
    fail_forecast: bool = False
    geocode_calls: int = 0
    forecast_calls: int = 0

    def __call__(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if "geocoding-api" in url:
            self.geocode_calls += 1
            if self.geocode_payload is None:
                return {"results": [{"latitude": 25.7617, "longitude": -80.1918}]}
            return self.geocode_payload

        if "forecast" in url:
            self.forecast_calls += 1
            if self.fail_forecast:
                raise RuntimeError("forecast provider unavailable")
            if self.forecast_payload is None:
                return {
                    "hourly": {
                        "time": ["2026-09-11T16:00", "2026-09-11T17:00"],
                        "temperature_2m": [86, 87],
                        "precipitation_probability": [25, 20],
                        "wind_speed_10m": [8, 9],
                        "weather_code": [1, 2],
                    }
                }
            return self.forecast_payload

        raise AssertionError(f"Unexpected URL: {url}")


class ScriptedModel:
    def __init__(self, scripted: list[ModelResponse]) -> None:
        self._scripted = scripted
        self._idx = 0

    def run(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        response = self._scripted[self._idx]
        self._idx += 1
        return response


class ScheduleOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        now = datetime.now(timezone.utc)

        with self.Session() as session:
            session.add(
                Customer(
                    id="cus_tamara",
                    first_name="Tamara",
                    last_name="Cruz",
                    company="Cruz Residence",
                )
            )
            session.add(
                Job(
                    id="job_future_1",
                    customer_id="cus_tamara",
                    description="AC maintenance",
                    work_status="scheduled",
                    scheduled_start=now + timedelta(days=2, hours=1),
                    scheduled_end=now + timedelta(days=2, hours=3),
                    city="Miami",
                    state="FL",
                    zip="33101",
                    street="123 Main St",
                )
            )
            session.add(
                Job(
                    id="job_future_2",
                    customer_id="cus_tamara",
                    description="Filter replacement",
                    work_status="scheduled",
                    scheduled_start=now + timedelta(days=3, hours=1),
                    scheduled_end=now + timedelta(days=3, hours=2),
                    city="Miami",
                    state="FL",
                    zip="33101",
                    street="123 Main St",
                )
            )
            session.add(JobEmployee(job_id="job_future_1", employee_id="emp_1"))
            session.add(Employee(id="emp_1", first_name="Jordan", last_name="Lee", role="Technician"))
            session.commit()

        with self.Session() as session:
            session.execute(AgentSession.__table__.insert().values(id=7001, started_at=now, channel="web", status="active"))
            session.commit()

        self.schedule = ScheduleOverrideService()
        self.registry = build_default_tool_registry(self.Session, FakeSemanticRetriever())

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_source_jobs_schedule_never_modified(self) -> None:
        with self.Session() as session:
            job_before = session.get(Job, "job_future_1")
            original_start = job_before.scheduled_start
            original_end = job_before.scheduled_end

            self.schedule.reschedule_job(
                session,
                job_id="job_future_1",
                new_start_at=original_start + timedelta(days=1),
                new_end_at=original_end + timedelta(days=1),
                duration_minutes=None,
                reason="Customer requested afternoon window",
                agent_session_id=7001,
                idempotency_key="k1",
            )

        with self.Session() as session:
            job_after = session.get(Job, "job_future_1")
            self.assertEqual(job_after.scheduled_start, original_start)
            self.assertEqual(job_after.scheduled_end, original_end)

    def test_override_creates_effective_schedule(self) -> None:
        payload = self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": miami_dt(2026, 9, 11, 14).isoformat(),
                "new_end_at": miami_dt(2026, 9, 11, 16).isoformat(),
                "reason": "Customer requested afternoon appointment",
                "agent_session_id": 7001,
                "idempotency_key": "retry-key-1",
            },
        ).payload

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["status"], "active")
        self.assertFalse(payload["data"]["idempotent"])

    def test_get_upcoming_appointments_and_get_job_use_effective_schedule(self) -> None:
        new_start = miami_dt(2026, 9, 11, 14)
        self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": new_start.isoformat(),
                "new_end_at": (new_start + timedelta(hours=2)).isoformat(),
                "reason": "Customer moved date",
                "agent_session_id": 7001,
                "idempotency_key": "retry-key-2",
            },
        )

        upcoming = self.registry.execute(
            "get_upcoming_appointments",
            {
                "customer_id": "cus_tamara",
                "limit": 5,
            },
        ).payload
        self.assertTrue(upcoming["ok"])
        self.assertEqual(upcoming["data"]["appointments"][1]["job_id"], "job_future_1")
        self.assertEqual(
            utc_aware(datetime.fromisoformat(upcoming["data"]["appointments"][1]["effective_start_at"])),
            new_start.astimezone(timezone.utc),
        )

        job_payload = self.registry.execute("get_job", {"job_id": "job_future_1"}).payload
        self.assertEqual(
            utc_aware(datetime.fromisoformat(job_payload["data"]["job"]["effective_start_at"])),
            new_start.astimezone(timezone.utc),
        )
        self.assertTrue(job_payload["data"]["job"]["has_schedule_override"])

    def test_preserves_duration_when_only_new_start_is_supplied(self) -> None:
        with self.Session() as session:
            job = session.get(Job, "job_future_1")
            original_duration = job.scheduled_end - job.scheduled_start

        new_start = miami_dt(2026, 9, 11, 14)
        payload = self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": new_start.isoformat(),
                "reason": "Customer asked for later start",
                "agent_session_id": 7001,
                "idempotency_key": "retry-key-3",
            },
        ).payload
        self.assertTrue(payload["ok"])

        new_end = utc_aware(datetime.fromisoformat(payload["data"]["effective_end_at"]))
        self.assertEqual(new_end - new_start.astimezone(timezone.utc), original_duration)

    def test_invalid_or_past_datetime_rejected(self) -> None:
        result = self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": miami_dt(2026, 9, 4, 14).isoformat(),
                "reason": "Bad date",
                "agent_session_id": 7001,
                "idempotency_key": "retry-key-4",
            },
        ).payload
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_schedule_time")

    def test_reschedule_interprets_model_datetimes_as_miami_wall_clock(self) -> None:
        result = self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc).isoformat(),
                "new_end_at": datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc).isoformat(),
                "reason": "Customer requested reschedule to September 11 at 2:00 PM.",
                "agent_session_id": 7001,
                "idempotency_key": "wall-clock-utc-1",
            },
        ).payload

        self.assertTrue(result["ok"])
        self.assertEqual(utc_aware(datetime.fromisoformat(result["data"]["effective_start_at"])), datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(utc_aware(datetime.fromisoformat(result["data"]["effective_end_at"])), datetime(2026, 9, 11, 19, 0, tzinfo=timezone.utc))

        with self.Session() as session:
            override = session.execute(
                select(JobScheduleOverride).where(JobScheduleOverride.job_id == "job_future_1").order_by(JobScheduleOverride.id.desc())
            ).scalars().first()
            self.assertEqual(utc_aware(override.new_start_at), datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc))
            self.assertEqual(utc_aware(override.new_end_at), datetime(2026, 9, 11, 19, 0, tzinfo=timezone.utc))

    def test_miami_round_trip_and_dst_safe_conversion(self) -> None:
        local_time = datetime(2026, 11, 2, 14, 0, tzinfo=MIAMI)
        utc_time = ScheduleOverrideService.coerce_miami_wall_time(local_time).astimezone(timezone.utc)
        self.assertEqual(utc_time, datetime(2026, 11, 2, 19, 0, tzinfo=timezone.utc))

    def test_repeated_identical_execution_is_idempotent(self) -> None:
        args = {
            "job_id": "job_future_1",
            "new_start_at": miami_dt(2026, 9, 11, 14).isoformat(),
            "new_end_at": miami_dt(2026, 9, 11, 16).isoformat(),
            "reason": "Retry-safe request",
            "agent_session_id": 7001,
            "idempotency_key": "idem-constant",
        }

        first = self.registry.execute("reschedule_appointment", args).payload
        second = self.registry.execute("reschedule_appointment", args).payload

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertFalse(first["data"]["idempotent"])
        self.assertTrue(second["data"]["idempotent"])

        with self.Session() as session:
            active_count = session.scalar(
                select(func.count())
                .select_from(JobScheduleOverride)
                .where(JobScheduleOverride.job_id == "job_future_1", JobScheduleOverride.status == "active")
            )
            self.assertEqual(active_count, 1)

    def test_agent_audit_logs_reschedule_action(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[
                    ModelToolCall(
                        call_id="call_1",
                        name="reschedule_appointment",
                        arguments={
                            "job_id": "job_future_1",
                            "new_start_at": miami_dt(2026, 9, 11, 14).isoformat(),
                            "new_end_at": miami_dt(2026, 9, 11, 16).isoformat(),
                            "reason": "Customer requested Friday 2 PM",
                        },
                    )
                ],
            ),
            ModelResponse(message="Tamara Cruz's appointment has been moved to Friday at 2:00 PM."),
        ]

        agent = AgentOrchestrator(
            model=ScriptedModel(scripted),
            registry=self.registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )

        result = agent.run("Move Tamara Cruz's next appointment to Friday at 2 PM.", channel="web")
        self.assertEqual(result.status, "completed")

        with self.Session() as session:
            actions = list(session.scalars(select(AgentAction).where(AgentAction.session_id == result.session_id)))
            self.assertEqual(actions[-1].action_type, "reschedule_appointment")
            self.assertIn("job_id", actions[-1].arguments)
            self.assertIn("effective_start_at", (actions[-1].result or {}).get("data", {}))

    def test_text_flow_uses_agent_tool_loop(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="c1", name="search_customers", arguments={"name": "Tamara Cruz"})],
            ),
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="c2", name="get_upcoming_appointments", arguments={})],
            ),
            ModelResponse(
                message=None,
                tool_calls=[
                    ModelToolCall(
                        call_id="c3",
                        name="reschedule_appointment",
                        arguments={
                            "job_id": "job_future_1",
                            "new_start_at": miami_dt(2026, 9, 11, 14).isoformat(),
                            "new_end_at": miami_dt(2026, 9, 11, 16).isoformat(),
                            "reason": "Customer asked for Friday afternoon",
                        },
                    )
                ],
            ),
            ModelResponse(message="Tamara Cruz's appointment has been moved to Friday, September 11 at 2:00 PM."),
        ]

        agent = AgentOrchestrator(
            model=ScriptedModel(scripted),
            registry=self.registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )

        first = agent.run("Move Tamara Cruz's next appointment to Friday at 2 PM.", channel="web", finalize_session_on_return=False)
        self.assertEqual(first.status, "active")

        followup_script = [
            ModelResponse(message=None, tool_calls=[ModelToolCall(call_id="c4", name="get_upcoming_appointments", arguments={})]),
            ModelResponse(message="Her next appointment is Friday at 2:00 PM."),
        ]
        agent_2 = AgentOrchestrator(
            model=ScriptedModel(followup_script),
            registry=self.registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )
        second = agent_2.run("When is her next appointment?", channel="web", session_id=first.session_id)
        self.assertIn("2:00 PM", second.response)

    def test_ambiguity_requires_clarification_not_mutation(self) -> None:
        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="c1", name="search_customers", arguments={"name": "Tamara Cruz"})],
            ),
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="c2", name="get_upcoming_appointments", arguments={})],
            ),
            ModelResponse(message="I found two upcoming appointments for Tamara. Which one should I move?"),
        ]

        agent = AgentOrchestrator(
            model=ScriptedModel(scripted),
            registry=self.registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )
        result = agent.run("Move Tamara Cruz's appointment to Friday afternoon.", channel="web")
        self.assertIn("which one", result.response.lower())

        with self.Session() as session:
            count = session.scalar(select(func.count()).select_from(JobScheduleOverride))
            self.assertEqual(count, 0)

    def test_dashboard_reflects_rescheduled_appointment(self) -> None:
        new_start = miami_dt(2026, 9, 11, 14)
        self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": new_start.isoformat(),
                "new_end_at": (new_start + timedelta(hours=2)).isoformat(),
                "reason": "Customer moved",
                "agent_session_id": 7001,
                "idempotency_key": "dash-1",
            },
        )

        dashboard = DashboardService(
            self.Session,
            schedule_service=ScheduleOverrideService(),
            weather_service=FakeWeatherService(),
        )
        summary = dashboard.get_summary()
        job = next(item for item in summary["appointments"] if item["job_id"] == "job_future_1")
        self.assertEqual(utc_aware(job["effective_start_at"]).astimezone(MIAMI), new_start)
        self.assertEqual(job["weather"]["temperature_f"], 87)

    def test_dashboard_returns_appointments_when_weather_unavailable(self) -> None:
        dashboard = DashboardService(
            self.Session,
            schedule_service=ScheduleOverrideService(),
            weather_service=FailingWeatherService(),
        )
        summary = dashboard.get_summary()
        self.assertGreaterEqual(len(summary["appointments"]), 1)
        self.assertIsNone(summary["appointments"][0]["weather"])

    def test_weather_tool_uses_effective_schedule_and_reuses_cache(self) -> None:
        weather_transport = ControlledWeatherHttp()
        weather_service = WeatherService(
            http_get_json=weather_transport,
            now_provider=lambda: datetime(2026, 9, 5, 10, 0, tzinfo=MIAMI_TZ),
        )
        registry = build_default_tool_registry(self.Session, FakeSemanticRetriever(), weather_service=weather_service)

        with self.Session() as session:
            original_job = session.get(Job, "job_future_1")
            original_start = original_job.scheduled_start
            original_end = original_job.scheduled_end

        new_start = datetime(2026, 9, 11, 14, 0, tzinfo=MIAMI_TZ)
        self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": new_start.isoformat(),
                "new_end_at": (new_start + timedelta(hours=2)).isoformat(),
                "reason": "Customer requested the later slot",
                "agent_session_id": 7001,
                "idempotency_key": "weather-tool-1",
            },
        )

        first = registry.execute("get_weather_forecast", {"job_id": "job_future_1"}).payload
        second = registry.execute("get_weather_forecast", {"job_id": "job_future_1"}).payload

        self.assertTrue(first["ok"])
        self.assertTrue(first["data"]["available"])
        self.assertEqual(datetime.fromisoformat(first["data"]["forecast_at"]), datetime(2026, 9, 11, 17, 0, tzinfo=MIAMI_TZ))
        self.assertEqual(weather_transport.geocode_calls, 1)
        self.assertEqual(weather_transport.forecast_calls, 1)
        self.assertEqual(second["data"]["forecast_at"], first["data"]["forecast_at"])

        with self.Session() as session:
            job = session.get(Job, "job_future_1")
            self.assertEqual(job.scheduled_start, original_start)
            self.assertEqual(job.scheduled_end, original_end)

    def test_weather_tool_returns_unavailable_when_provider_fails(self) -> None:
        weather_transport = ControlledWeatherHttp(fail_forecast=True)
        weather_service = WeatherService(
            http_get_json=weather_transport,
            now_provider=lambda: datetime(2026, 9, 5, 10, 0, tzinfo=MIAMI_TZ),
        )
        registry = build_default_tool_registry(self.Session, FakeSemanticRetriever(), weather_service=weather_service)

        with self.Session() as session:
            job = session.get(Job, "job_future_1")
            job.latitude = Decimal("25.7617")
            job.longitude = Decimal("-80.1918")
            session.commit()

        result = registry.execute("get_weather_forecast", {"job_id": "job_future_1"}).payload

        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["available"])
        self.assertEqual(result["data"]["reason"], "forecast_unavailable")
        self.assertEqual(weather_transport.forecast_calls, 1)

    def test_weather_tool_returns_unavailable_beyond_horizon(self) -> None:
        weather_transport = ControlledWeatherHttp()
        weather_service = WeatherService(
            http_get_json=weather_transport,
            now_provider=lambda: datetime(2026, 9, 5, 10, 0, tzinfo=MIAMI_TZ),
        )
        registry = build_default_tool_registry(self.Session, FakeSemanticRetriever(), weather_service=weather_service)

        result = registry.execute(
            "get_weather_forecast",
            {
                "job_id": "job_future_1",
                "forecast_at": datetime(2026, 9, 20, 14, 0, tzinfo=MIAMI_TZ).isoformat(),
            },
        ).payload

        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["available"])
        self.assertEqual(result["data"]["reason"], "forecast_outside_provider_horizon")
        self.assertEqual(weather_transport.geocode_calls, 0)
        self.assertEqual(weather_transport.forecast_calls, 0)

    def test_agent_reschedule_then_weather_lookup_and_audit(self) -> None:
        weather_transport = ControlledWeatherHttp()
        weather_service = WeatherService(
            http_get_json=weather_transport,
            now_provider=lambda: datetime(2026, 9, 5, 10, 0, tzinfo=MIAMI_TZ),
        )
        registry = build_default_tool_registry(self.Session, FakeSemanticRetriever(), weather_service=weather_service)

        scripted = [
            ModelResponse(
                message=None,
                tool_calls=[
                    ModelToolCall(
                        call_id="c1",
                        name="reschedule_appointment",
                        arguments={
                            "job_id": "job_future_1",
                            "new_start_at": datetime(2026, 9, 11, 14, 0, tzinfo=MIAMI_TZ).isoformat(),
                            "new_end_at": datetime(2026, 9, 11, 16, 0, tzinfo=MIAMI_TZ).isoformat(),
                            "reason": "Customer requested Friday afternoon",
                        },
                    )
                ],
            ),
            ModelResponse(
                message=None,
                tool_calls=[ModelToolCall(call_id="c2", name="get_weather_forecast", arguments={})],
            ),
            ModelResponse(message="Done — the appointment has been moved. The forecast looks partly cloudy."),
        ]

        agent = AgentOrchestrator(
            model=ScriptedModel(scripted),
            registry=registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )

        result = agent.run("Move Tamara Cruz's appointment to Friday at 2 PM.", channel="web")

        self.assertEqual(result.status, "completed")
        self.assertIn("forecast", result.response.lower())
        self.assertEqual(len(result.tool_calls), 2)
        self.assertEqual(result.tool_calls[1].tool_name, "get_weather_forecast")
        self.assertEqual(result.tool_calls[1].arguments["job_id"], "job_future_1")
        self.assertEqual(weather_transport.forecast_calls, 1)

        dashboard = DashboardService(self.Session, schedule_service=ScheduleOverrideService(), weather_service=weather_service)
        detail = dashboard.get_session_detail(result.session_id)
        action_types = [action["action_type"] for action in detail["actions"]]
        self.assertIn("get_weather_forecast", action_types)
        weather_action = next(action for action in detail["actions"] if action["action_type"] == "get_weather_forecast")
        self.assertEqual(weather_action["display_label"], "Checked weather forecast")
        self.assertIn("Partly cloudy", weather_action["detail"])

    def test_weather_followup_uses_contextual_job(self) -> None:
        weather_transport = ControlledWeatherHttp()
        weather_service = WeatherService(
            http_get_json=weather_transport,
            now_provider=lambda: datetime(2026, 9, 5, 10, 0, tzinfo=MIAMI_TZ),
        )
        registry = build_default_tool_registry(self.Session, FakeSemanticRetriever(), weather_service=weather_service)

        first_script = [
            ModelResponse(message=None, tool_calls=[ModelToolCall(call_id="c1", name="search_customers", arguments={"name": "Tamara Cruz"})]),
            ModelResponse(message=None, tool_calls=[ModelToolCall(call_id="c2", name="get_upcoming_appointments", arguments={})]),
            ModelResponse(message="Her next appointment is Friday at 2 PM."),
        ]
        agent_1 = AgentOrchestrator(
            model=ScriptedModel(first_script),
            registry=registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )
        first = agent_1.run("When is Tamara's next appointment?", channel="web")
        self.assertEqual(first.status, "completed")

        second_script = [
            ModelResponse(message=None, tool_calls=[ModelToolCall(call_id="c2", name="get_weather_forecast", arguments={})]),
            ModelResponse(message="It should be partly cloudy and around 86 degrees."),
        ]
        agent_2 = AgentOrchestrator(
            model=ScriptedModel(second_script),
            registry=registry,
            session_service=AgentSessionService(self.Session),
            action_service=AgentActionService(self.Session),
            max_tool_rounds=8,
            max_history_messages=20,
        )
        second = agent_2.run("Will it rain during her appointment?", channel="web", session_id=first.session_id)

        self.assertEqual(second.tool_calls[0].tool_name, "get_weather_forecast")
        self.assertEqual(second.tool_calls[0].arguments["job_id"], "job_future_1")
        self.assertIn("partly cloudy", second.response.lower())

    def test_dashboard_appointments_search_and_pagination(self) -> None:
        new_start = miami_dt(2026, 9, 11, 14)
        self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": new_start.isoformat(),
                "new_end_at": (new_start + timedelta(hours=2)).isoformat(),
                "reason": "Customer moved",
                "agent_session_id": 7001,
                "idempotency_key": "appointments-1",
            },
        )

        dashboard = DashboardService(
            self.Session,
            schedule_service=ScheduleOverrideService(),
            weather_service=FakeWeatherService(),
        )
        page = dashboard.list_appointments(
            q="Jordan",
            from_at=miami_dt(2026, 9, 1, 0),
            to_at=miami_dt(2026, 9, 30, 23, 59),
            limit=1,
            offset=0,
        )

        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["job_id"], "job_future_1")
        self.assertTrue(page["items"][0]["has_schedule_override"])
        self.assertEqual(page["items"][0]["technicians"], ["Jordan Lee"])

    def test_dashboard_schedule_changes_history_endpoint_shape(self) -> None:
        first_start = miami_dt(2026, 9, 12, 14)
        second_start = miami_dt(2026, 9, 13, 14)

        self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": first_start.isoformat(),
                "new_end_at": (first_start + timedelta(hours=2)).isoformat(),
                "reason": "First move",
                "agent_session_id": 7001,
                "idempotency_key": "changes-1",
            },
        )
        self.registry.execute(
            "reschedule_appointment",
            {
                "job_id": "job_future_1",
                "new_start_at": second_start.isoformat(),
                "new_end_at": (second_start + timedelta(hours=2)).isoformat(),
                "reason": "Second move",
                "agent_session_id": 7001,
                "idempotency_key": "changes-2",
            },
        )

        dashboard = DashboardService(self.Session, schedule_service=ScheduleOverrideService(), weather_service=FakeWeatherService())
        changes = dashboard.list_schedule_changes(q="second", limit=10, offset=0)

        self.assertGreaterEqual(changes["total"], 1)
        self.assertEqual(changes["items"][0]["job_id"], "job_future_1")
        self.assertEqual(changes["items"][0]["status"], "active")
        self.assertEqual(changes["items"][0]["channel"], "web")
        self.assertIn("Gulf Breeze AI", changes["items"][0]["changed_by"])


if __name__ == "__main__":
    unittest.main()
