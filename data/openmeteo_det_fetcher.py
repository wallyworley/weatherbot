"""Shared Open-Meteo deterministic temperature fetcher.

Open-Meteo exposes GFS and ECMWF with the same JSON shape. This module keeps
the station/date/lead-hour handling identical so model accuracy comparisons are
about the models, not about ingestion differences.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from weather_bot.config import Station

HORIZON_DAYS = 7


def latest_six_hour_cycle(now: datetime | None = None) -> datetime:
    """Most recent 00/06/12/18 UTC cycle."""
    now = now or datetime.now(tz=timezone.utc)
    h = (now.hour // 6) * 6
    return now.replace(hour=h, minute=0, second=0, microsecond=0)


def fetch_tmp_series(
    *,
    station: Station,
    model: str,
    url: str,
    horizon_days: int = HORIZON_DAYS,
    run_time: datetime | None = None,
) -> list[dict]:
    """Pull hourly 2m temperature from an Open-Meteo endpoint.

    Returns rows compatible with ``det_forecast``.
    """
    run_time = run_time or latest_six_hour_cycle()
    today_local = datetime.now(tz=ZoneInfo(station.tz)).date()
    start = today_local
    end = today_local + timedelta(days=horizon_days)

    params = {
        "latitude": station.lat,
        "longitude": station.lon,
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    times = j["hourly"]["time"]
    temps = j["hourly"]["temperature_2m"]

    rows: list[dict] = []
    for t_str, value in zip(times, temps):
        if value is None:
            continue
        valid_time = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
        lead_hr = int((valid_time - run_time).total_seconds() // 3600)
        if lead_hr < 0:
            continue
        rows.append(
            {
                "station": station.code,
                "model": model,
                "run_time": run_time,
                "valid_time": valid_time,
                "lead_hr": lead_hr,
                "var": "TMP_2M",
                "value": float(value),
            }
        )
    return rows
