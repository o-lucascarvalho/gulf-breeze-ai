from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.db.models import Job
from app.services.weather import MIAMI_TZ, WeatherService


class WeatherServiceTests(unittest.TestCase):
    def _job(self, *, zip_code: str | None = "33101", city: str | None = "Miami", state: str | None = "FL") -> Job:
        return Job(
            id="job_weather_1",
            customer_id="cus_1",
            description="AC maintenance",
            scheduled_start=datetime.now(ZoneInfo("UTC")) + timedelta(days=1),
            city=city,
            state=state,
            zip=zip_code,
            street="123 Main St",
            latitude=Decimal("25.7617"),
            longitude=Decimal("-80.1918"),
        )

    def test_enrichment_with_resolvable_location(self) -> None:
        target = datetime(2026, 9, 8, 10, 30, tzinfo=MIAMI_TZ)
        calls = {"geocode": 0, "forecast": 0}

        def fake_http(url: str, params: dict[str, Any]) -> dict[str, Any]:
            if "forecast" in url:
                calls["forecast"] += 1
                return {
                    "hourly": {
                        "time": ["2026-09-08T10:00", "2026-09-08T11:00"],
                        "temperature_2m": [87, 88],
                        "precipitation_probability": [20, 25],
                        "wind_speed_10m": [11, 12],
                        "weather_code": [2, 1],
                    }
                }
            if "geocoding-api" in url:
                calls["geocode"] += 1
                return {"results": [{"latitude": 25.77, "longitude": -80.19}]}
            raise AssertionError("Unexpected URL")

        service = WeatherService(http_get_json=fake_http, now_provider=lambda: datetime(2026, 9, 7, 8, 0, tzinfo=MIAMI_TZ))
        job = self._job()
        job.latitude = None
        job.longitude = None
        weather = service.get_weather_for_appointment(job, target)

        self.assertIsNotNone(weather)
        self.assertEqual(weather["temperature_f"], 87)
        self.assertEqual(weather["condition"], "Partly cloudy")
        self.assertEqual(weather["location_precision"], "zip")
        self.assertEqual(calls["geocode"], 1)
        self.assertEqual(calls["forecast"], 1)

    def test_nearest_hour_is_selected(self) -> None:
        target = datetime(2026, 9, 8, 10, 45, tzinfo=MIAMI_TZ)

        def fake_http(url: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "hourly": {
                    "time": ["2026-09-08T09:00", "2026-09-08T11:00", "2026-09-08T14:00"],
                    "temperature_2m": [84, 89, 93],
                    "precipitation_probability": [10, 50, 70],
                    "wind_speed_10m": [7, 14, 23],
                    "weather_code": [1, 3, 63],
                }
            }

        service = WeatherService(http_get_json=fake_http, now_provider=lambda: datetime(2026, 9, 7, 8, 0, tzinfo=MIAMI_TZ))
        weather = service.get_weather_for_appointment(self._job(), target)

        self.assertIsNotNone(weather)
        self.assertEqual(weather["temperature_f"], 89)

    def test_provider_failure_returns_none(self) -> None:
        def failing_http(url: str, params: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("provider down")

        service = WeatherService(http_get_json=failing_http)
        weather = service.get_weather_for_appointment(self._job(), datetime.now(MIAMI_TZ) + timedelta(days=1))
        self.assertIsNone(weather)

    def test_cache_prevents_duplicate_calls(self) -> None:
        counters = {"calls": 0}

        def fake_http(url: str, params: dict[str, Any]) -> dict[str, Any]:
            counters["calls"] += 1
            return {
                "hourly": {
                    "time": ["2026-09-08T10:00"],
                    "temperature_2m": [87],
                    "precipitation_probability": [20],
                    "wind_speed_10m": [11],
                    "weather_code": [2],
                }
            }

        service = WeatherService(
            http_get_json=fake_http,
            now_provider=lambda: datetime(2026, 9, 7, 8, 0, tzinfo=MIAMI_TZ),
            cache_ttl_seconds=3600,
        )
        job = self._job()
        target = datetime(2026, 9, 8, 10, 30, tzinfo=MIAMI_TZ)

        service.get_weather_for_appointment(job, target)
        service.get_weather_for_appointment(job, target)

        self.assertEqual(counters["calls"], 1)

    def test_beyond_horizon_returns_unavailable(self) -> None:
        def fake_http(url: str, params: dict[str, Any]) -> dict[str, Any]:
            return {
                "hourly": {
                    "time": ["2026-09-08T10:00"],
                    "temperature_2m": [87],
                    "precipitation_probability": [20],
                    "wind_speed_10m": [11],
                    "weather_code": [2],
                }
            }

        service = WeatherService(http_get_json=fake_http)
        weather = service.get_weather_for_appointment(
            self._job(),
            datetime(2026, 9, 12, 10, 0, tzinfo=MIAMI_TZ),
        )
        self.assertIsNone(weather)

    def test_unresolvable_address_returns_none(self) -> None:
        calls = {"count": 0}

        def fake_http(url: str, params: dict[str, Any]) -> dict[str, Any]:
            calls["count"] += 1
            return {"results": []}

        service = WeatherService(http_get_json=fake_http)
        job = self._job(zip_code=None, city=None, state=None)
        job.latitude = None
        job.longitude = None
        weather = service.get_weather_for_appointment(job, datetime.now(MIAMI_TZ) + timedelta(days=1))
        self.assertIsNone(weather)
        self.assertEqual(calls["count"], 0)


if __name__ == "__main__":
    unittest.main()
