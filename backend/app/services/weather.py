from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from app.db.models import Job

LOGGER = logging.getLogger("uvicorn.error")
MIAMI_TZ = ZoneInfo("America/New_York")


@dataclass
class _CacheEntry:
    expires_at: datetime
    value: Any


class WeatherService:
    def __init__(
        self,
        *,
        timeout_seconds: float = 3.0,
        cache_ttl_seconds: int = 2700,
        now_provider=lambda: datetime.now(MIAMI_TZ),
        http_get_json=None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._now_provider = now_provider
        self._http_get_json = http_get_json or self._default_http_get_json
        self._cache: dict[str, _CacheEntry] = {}

    def build_forecast_report(self, job: Job, forecast_at: datetime | None) -> dict[str, Any]:
        target = forecast_at or job.scheduled_start
        location_label = self._address_label(job)

        if target is None:
            return self._unavailable_report(
                job_id=job.id,
                forecast_at=None,
                location=location_label,
                location_precision=None,
                reason="missing_forecast_time",
            )

        if target.tzinfo is None:
            target = target.replace(tzinfo=MIAMI_TZ)
        target = target.astimezone(MIAMI_TZ)

        now = self._now_provider()
        if target < now - timedelta(hours=1):
            return self._unavailable_report(
                job_id=job.id,
                forecast_at=target,
                location=location_label,
                location_precision=location_precision,
                reason="forecast_time_in_past",
            )

        if target > now + timedelta(days=7):
            return self._unavailable_report(
                job_id=job.id,
                forecast_at=target,
                location=location_label,
                location_precision=None,
                reason="forecast_outside_provider_horizon",
            )

        location = self._resolve_location(job)
        location_label = location["label"] if location else location_label
        location_precision = location["precision"] if location else None

        if location is None:
            return self._unavailable_report(
                job_id=job.id,
                forecast_at=target,
                location=location_label,
                location_precision=None,
                reason="location_unavailable",
            )

        hourly = self._fetch_hourly_forecast(location)
        if not hourly:
            return self._unavailable_report(
                job_id=job.id,
                forecast_at=target,
                location=location_label,
                location_precision=location_precision,
                reason="forecast_unavailable",
            )

        selected = self._pick_nearest_hour(hourly, target)
        if selected is None:
            return self._unavailable_report(
                job_id=job.id,
                forecast_at=target,
                location=location_label,
                location_precision=location_precision,
                reason="forecast_outside_provider_horizon",
            )

        return {
            "job_id": job.id,
            "forecast_at": selected["forecast_at"].isoformat(),
            "location": location_label,
            "location_precision": location_precision,
            "temperature_f": selected["temperature_f"],
            "precipitation_probability": selected["precipitation_probability"],
            "wind_mph": selected["wind_mph"],
            "condition": selected["condition"],
            "risk_indicator": self._risk_indicator(selected),
            "available": True,
        }

    def get_weather_for_appointment(self, job: Job, effective_start_at: datetime | None) -> dict[str, Any] | None:
        report = self.build_forecast_report(job, effective_start_at)
        if not report.get("available"):
            return None
        return {
            "temperature_f": report["temperature_f"],
            "precipitation_probability": report["precipitation_probability"],
            "wind_mph": report["wind_mph"],
            "condition": report["condition"],
            "forecast_at": report["forecast_at"],
            "location_precision": report["location_precision"],
            "risk_indicator": report.get("risk_indicator"),
        }

    def _resolve_location(self, job: Job) -> dict[str, Any] | None:
        if job.latitude is not None and job.longitude is not None:
            return {
                "latitude": float(Decimal(job.latitude)),
                "longitude": float(Decimal(job.longitude)),
                "precision": "coordinates",
                "label": self._address_label(job),
            }

        if job.zip:
            geocoded = self._cached(
                f"geocode:zip:{job.zip}",
                lambda: self._lookup_geocode({"name": job.zip, "count": 1, "language": "en", "countryCode": "US"}),
            )
            if geocoded:
                geocoded["precision"] = "zip"
                geocoded["label"] = self._address_label(job)
                return geocoded

        if job.city and job.state:
            geocoded = self._cached(
                f"geocode:city:{job.city}:{job.state}",
                lambda: self._lookup_geocode({"name": f"{job.city}, {job.state}", "count": 1, "language": "en", "countryCode": "US"}),
            )
            if geocoded:
                geocoded["precision"] = "city_state"
                geocoded["label"] = self._address_label(job)
                return geocoded

        return None

    def _lookup_geocode(self, params: dict[str, Any]) -> dict[str, Any] | None:
        try:
            payload = self._http_get_json("https://geocoding-api.open-meteo.com/v1/search", params)
            rows = payload.get("results") or []
            if not rows:
                return None
            row = rows[0]
            return {
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
            }
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("weather_geocode_failed error=%s", exc)
            return None

    def _fetch_hourly_forecast(self, location: dict[str, Any]) -> list[dict[str, Any]]:
        lat = location["latitude"]
        lon = location["longitude"]
        key = f"forecast:{round(lat, 2)}:{round(lon, 2)}"

        def _loader() -> list[dict[str, Any]]:
            payload = self._http_get_json(
                "https://api.open-meteo.com/v1/forecast",
                {
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,weather_code",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "timezone": "America/New_York",
                    "forecast_days": 7,
                },
            )
            hourly = payload.get("hourly") or {}
            times = hourly.get("time") or []
            temps = hourly.get("temperature_2m") or []
            precip = hourly.get("precipitation_probability") or []
            wind = hourly.get("wind_speed_10m") or []
            codes = hourly.get("weather_code") or []

            rows = []
            for idx, ts in enumerate(times):
                if idx >= len(temps) or idx >= len(precip) or idx >= len(wind) or idx >= len(codes):
                    continue
                forecast_at = datetime.fromisoformat(ts).replace(tzinfo=MIAMI_TZ)
                rows.append(
                    {
                        "forecast_at": forecast_at,
                        "temperature_f": int(round(float(temps[idx]))),
                        "precipitation_probability": int(round(float(precip[idx]))),
                        "wind_mph": int(round(float(wind[idx]))),
                        "condition": self._weather_code_to_label(int(codes[idx])),
                    }
                )
            return rows

        try:
            return self._cached(key, _loader)
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("weather_forecast_failed error=%s", exc)
            return []

    @staticmethod
    def _pick_nearest_hour(rows: list[dict[str, Any]], target: datetime) -> dict[str, Any] | None:
        if not rows:
            return None

        by_distance = sorted(rows, key=lambda row: abs((row["forecast_at"] - target).total_seconds()))
        best = by_distance[0]
        if abs((best["forecast_at"] - target).total_seconds()) > 4 * 3600:
            return None
        return best

    def _cached(self, key: str, loader):
        now = self._now_provider()
        cached = self._cache.get(key)
        if cached and cached.expires_at > now:
            return cached.value

        value = loader()
        self._cache[key] = _CacheEntry(expires_at=now + timedelta(seconds=self._cache_ttl_seconds), value=value)
        return value

    @staticmethod
    def _weather_code_to_label(code: int) -> str:
        labels = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Rime fog",
            51: "Light drizzle",
            53: "Drizzle",
            55: "Dense drizzle",
            61: "Light rain",
            63: "Rain",
            65: "Heavy rain",
            71: "Snow",
            80: "Rain showers",
            95: "Thunderstorm",
        }
        return labels.get(code, "Forecast available")

    @staticmethod
    def _risk_indicator(row: dict[str, Any]) -> str | None:
        if row["precipitation_probability"] >= 60:
            return "Rain likely"
        if row["temperature_f"] >= 92:
            return "High heat"
        if row["wind_mph"] >= 20:
            return "Strong wind"
        return None

    @staticmethod
    def _unavailable_report(
        *,
        job_id: str,
        forecast_at: datetime | None,
        location: str | None,
        location_precision: str | None,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "forecast_at": forecast_at.isoformat() if forecast_at is not None else None,
            "location": location,
            "location_precision": location_precision,
            "available": False,
            "reason": reason,
        }

    @staticmethod
    def _address_label(job: Job) -> str | None:
        parts = [part for part in [job.street, job.city, job.state, job.zip] if part]
        return ", ".join(parts) if parts else None

    def _default_http_get_json(self, base_url: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{base_url}?{urlencode(params)}"
        with urlopen(url, timeout=self._timeout_seconds) as response:  # nosec: B310 - expected public API URL
            payload = response.read().decode("utf-8")
            return json.loads(payload)
