"""WeatherNext 2 ensemble fetcher.

WeatherNext lives in BigQuery/Analytics Hub, so this module reuses the research
adapter and persists member-level hourly 2m temperatures into
``ensemble_forecast``. It is shadow/research-only until replay proves value.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from weather_bot.config import ACTIVE_STATIONS
from weather_bot.data import openmeteo_det_fetcher, persistence
from research.sources import weathernext_fetcher

log = logging.getLogger(__name__)

MODEL = "WEATHERNEXT2"
HORIZON_DAYS = 7


def fetch_weathernext_tmp_series(
    station: str,
    *,
    run_time: datetime | None = None,
    horizon_days: int = HORIZON_DAYS,
    table: str | None = None,
) -> list[dict]:
    """Fetch WeatherNext hourly 2m temperature members as ensemble_forecast rows."""
    run_time = run_time or openmeteo_det_fetcher.latest_six_hour_cycle()
    hours = weathernext_fetcher.fetch_hourly_ensemble(station, run_time, table=table)
    max_lead_hr = horizon_days * 24
    rows: list[dict] = []
    for hour in hours:
        valid_time = hour.valid_time
        if valid_time.tzinfo is None:
            valid_time = valid_time.replace(tzinfo=timezone.utc)
        else:
            valid_time = valid_time.astimezone(timezone.utc)
        lead_hr = int((valid_time - run_time).total_seconds() // 3600)
        if lead_hr < 0 or lead_hr > max_lead_hr:
            continue
        rows.append(
            {
                "station": station,
                "model": MODEL,
                "run_time": run_time,
                "valid_time": valid_time,
                "lead_hr": lead_hr,
                "var": "TMP_2M",
                "member": str(hour.ensemble_member),
                "value": float(hour.temp_f),
            }
        )
    return rows


def run(
    *,
    cycle: datetime | None = None,
    stations: list[str] | None = None,
    horizon_days: int = HORIZON_DAYS,
    table: str | None = None,
) -> int:
    cycles = [cycle] if cycle is not None else [
        openmeteo_det_fetcher.latest_six_hour_cycle() - timedelta(hours=6 * i)
        for i in range(0, 5)
    ]
    stations = stations or ACTIVE_STATIONS
    all_rows: list[dict] = []
    for station in stations:
        rows = []
        for try_cycle in cycles:
            log.info("WeatherNext2: station=%s cycle=%s", station, try_cycle)
            try:
                rows = fetch_weathernext_tmp_series(
                    station,
                    run_time=try_cycle,
                    horizon_days=horizon_days,
                    table=table,
                )
            except Exception as exc:
                log.warning("WeatherNext2 fetch failed for %s cycle=%s: %s", station, try_cycle, exc)
                continue
            if rows:
                break
        all_rows.extend(rows)
    if all_rows:
        persistence.upsert_ensemble_forecast(all_rows)
    log.info("Persisted %d WeatherNext2 rows", len(all_rows))
    return len(all_rows)
