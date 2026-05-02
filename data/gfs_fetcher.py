"""GFS hourly forecast fetcher (via Open-Meteo).

We pull GFS via Open-Meteo's /v1/gfs endpoint instead of NOAA S3 GRIB
because:
- Open-Meteo serves the latest run as parsed JSON (no GRIB byte-range plumbing)
- Comparison data (research/reports/) showed GFS via Open-Meteo achieves
  MAE 1.05–1.24°F across our fetch stations vs NBM 1.56–2.85 — competitive
  with what direct GRIB ingestion would give us, at a fraction of the code.
- Falls within the same det_forecast schema used by HRRR, so downstream
  consumers see GFS as just another deterministic model.

If Open-Meteo's archive proves insufficient (rate limits, latency, or
coverage gaps), this can be swapped for a NOAA S3 GRIB path later — the
table contract stays the same.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from weather_bot.config import ACTIVE_STATIONS, STATIONS, Station
from weather_bot.data import persistence

log = logging.getLogger(__name__)

OM_GFS_URL = "https://api.open-meteo.com/v1/gfs"
HORIZON_DAYS = 7   # GFS public horizon is 16d but for daily-temp markets we need ~7


def _latest_gfs_cycle(now: datetime | None = None) -> datetime:
    """Most recent GFS run time (00/06/12/18 UTC)."""
    now = now or datetime.now(tz=timezone.utc)
    h = (now.hour // 6) * 6
    return now.replace(hour=h, minute=0, second=0, microsecond=0)


def fetch_gfs_tmp_series(station: Station, horizon_days: int = HORIZON_DAYS,
                          run_time: datetime | None = None) -> list[dict]:
    """Pull hourly 2m temp from Open-Meteo's GFS, return det_forecast rows."""
    run_time = run_time or _latest_gfs_cycle()
    today_local = datetime.now(tz=ZoneInfo(station.tz)).date()
    start = today_local
    end = today_local + timedelta(days=horizon_days)

    params = {
        "latitude": station.lat,
        "longitude": station.lon,
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": "UTC",   # ask for UTC times so valid_time is straightforward
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    r = requests.get(OM_GFS_URL, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    times = j["hourly"]["time"]
    temps = j["hourly"]["temperature_2m"]

    rows: list[dict] = []
    for t_str, v in zip(times, temps):
        if v is None:
            continue
        valid_time = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
        lead_hr = int((valid_time - run_time).total_seconds() // 3600)
        if lead_hr < 0:
            continue   # past hour — stale
        rows.append(dict(
            station=station.code,
            model="GFS",
            run_time=run_time,
            valid_time=valid_time,
            lead_hr=lead_hr,
            var="TMP_2M",
            value=float(v),
        ))
    return rows


def run(cycle: datetime | None = None) -> None:
    cycle = cycle or _latest_gfs_cycle()
    all_rows: list[dict] = []
    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        log.info("GFS: station=%s cycle=%s", code, cycle)
        try:
            rows = fetch_gfs_tmp_series(station, run_time=cycle)
        except Exception as exc:
            log.warning("GFS fetch failed for %s: %s", code, exc)
            continue
        all_rows.extend(rows)
    if all_rows:
        persistence.upsert_det_forecast(all_rows)
        log.info("Persisted %d GFS rows", len(all_rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
