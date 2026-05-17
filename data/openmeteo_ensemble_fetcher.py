"""Open-Meteo true ensemble temperature fetcher.

The ensemble API returns ``temperature_2m`` for the control member plus
``temperature_2m_memberNN`` keys for perturbed members. We keep each member as a
separate row so research can use the empirical distribution directly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from weather_bot.config import ACTIVE_STATIONS, STATIONS, Station
from weather_bot.data import openmeteo_det_fetcher, persistence

log = logging.getLogger(__name__)

OM_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
HORIZON_DAYS = 7

ENSEMBLE_MODELS = {
    "GFS_ENS": "gfs025",
    "ECMWF_IFS_ENS": "ecmwf_ifs025",
    "ECMWF_AIFS_ENS": "ecmwf_aifs025",
}


def _member_from_key(key: str) -> str | None:
    if key == "temperature_2m":
        return "control"
    prefix = "temperature_2m_"
    if key.startswith(prefix):
        return key.removeprefix(prefix)
    return None


def fetch_ensemble_tmp_series(
    *,
    station: Station,
    model: str,
    openmeteo_model: str,
    horizon_days: int = HORIZON_DAYS,
    run_time: datetime | None = None,
) -> list[dict]:
    """Pull hourly 2m temperature for all returned ensemble members."""
    run_time = run_time or openmeteo_det_fetcher.latest_six_hour_cycle()
    today_local = datetime.now(tz=ZoneInfo(station.tz)).date()
    start = today_local
    end = today_local + timedelta(days=horizon_days)

    params = {
        "latitude": station.lat,
        "longitude": station.lon,
        "hourly": "temperature_2m",
        "models": openmeteo_model,
        "temperature_unit": "fahrenheit",
        "timezone": "UTC",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    r = requests.get(OM_ENSEMBLE_URL, params=params, timeout=45)
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(j.get("reason") or f"Open-Meteo ensemble error for {openmeteo_model}")

    hourly = j["hourly"]
    times = hourly["time"]
    rows: list[dict] = []
    for key, series in hourly.items():
        member = _member_from_key(key)
        if member is None:
            continue
        for t_str, value in zip(times, series):
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
                    "member": member,
                    "value": float(value),
                }
            )
    return rows


def run(
    *,
    cycle: datetime | None = None,
    models: dict[str, str] | None = None,
    horizon_days: int = HORIZON_DAYS,
) -> None:
    cycle = cycle or openmeteo_det_fetcher.latest_six_hour_cycle()
    models = models or ENSEMBLE_MODELS
    all_rows: list[dict] = []
    for model, om_model in models.items():
        for code in ACTIVE_STATIONS:
            station = STATIONS[code]
            log.info("ENSEMBLE: model=%s station=%s cycle=%s", model, code, cycle)
            try:
                rows = fetch_ensemble_tmp_series(
                    station=station,
                    model=model,
                    openmeteo_model=om_model,
                    horizon_days=horizon_days,
                    run_time=cycle,
                )
            except Exception as exc:
                log.warning("Ensemble fetch failed for %s %s: %s", model, code, exc)
                continue
            all_rows.extend(rows)
    if all_rows:
        persistence.upsert_ensemble_forecast(all_rows)
        log.info("Persisted %d ensemble member rows", len(all_rows))
