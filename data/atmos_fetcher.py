"""Atmospheric signals fetcher (Open-Meteo GFS).

Pulls 5 features per active fetch station, hourly out to 48h:
  - boundary_layer_height (m)         — depth of mixing layer; deep = strong convective heating
  - temperature_850hPa (°F)           — temp at ~1500m, indicates warm/cold air advection aloft
  - temperature_925hPa (°F)           — temp at ~750m, near-surface advection
  - cloud_cover (%)                   — afternoon sun suppression
  - shortwave_radiation (W/m²)        — realized incoming solar

These are inputs for the reversal-risk score (Sprint 3) and direct features
for diagnosing why the bot's TMAX forecast might over- or under-shoot.

Stored in `atmosphere_signals` table, keyed on (station, run_time, valid_time).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

import requests

from weather_bot.config import ACTIVE_FETCH_STATIONS, STATIONS, Station
from weather_bot.data import persistence

log = logging.getLogger(__name__)

OM_GFS_URL = "https://api.open-meteo.com/v1/gfs"
VARIABLES = [
    "boundary_layer_height",
    "temperature_850hPa",
    "temperature_925hPa",
    "cloud_cover",
    "shortwave_radiation",
]


def fetch_for_station(station: Station, forecast_days: int = 2,
                       run_time: datetime | None = None) -> list[dict]:
    """Pull hourly atmospheric signals for one station. Returns one row per hour."""
    run_time = run_time or datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    params = {
        "latitude": station.lat,
        "longitude": station.lon,
        "hourly": ",".join(VARIABLES),
        "temperature_unit": "fahrenheit",
        "timezone": "UTC",
        "forecast_days": forecast_days,
    }
    r = requests.get(OM_GFS_URL, params=params, timeout=30)
    r.raise_for_status()
    h = r.json().get("hourly", {})
    times = h.get("time", [])
    out: list[dict] = []
    for i, t_str in enumerate(times):
        valid_time = datetime.fromisoformat(t_str).replace(tzinfo=timezone.utc)
        out.append({
            "station": station.code,
            "valid_time": valid_time,
            "run_time": run_time,
            "bl_height_m":     _g(h, "boundary_layer_height", i),
            "tmp_850_f":       _g(h, "temperature_850hPa", i),
            "tmp_925_f":       _g(h, "temperature_925hPa", i),
            "cloud_cover_pct": _g(h, "cloud_cover", i),
            "solar_w_m2":      _g(h, "shortwave_radiation", i),
        })
    return out


def _g(h: dict, key: str, i: int):
    arr = h.get(key, [])
    if i >= len(arr):
        return None
    v = arr[i]
    return float(v) if v is not None else None


def upsert(rows: Iterable[dict]) -> None:
    sql = """
    INSERT INTO atmosphere_signals
      (station, valid_time, run_time, bl_height_m, tmp_850_f, tmp_925_f,
       cloud_cover_pct, solar_w_m2)
    VALUES (%(station)s, %(valid_time)s, %(run_time)s, %(bl_height_m)s,
            %(tmp_850_f)s, %(tmp_925_f)s, %(cloud_cover_pct)s, %(solar_w_m2)s)
    ON CONFLICT (station, run_time, valid_time) DO UPDATE SET
      bl_height_m     = EXCLUDED.bl_height_m,
      tmp_850_f       = EXCLUDED.tmp_850_f,
      tmp_925_f       = EXCLUDED.tmp_925_f,
      cloud_cover_pct = EXCLUDED.cloud_cover_pct,
      solar_w_m2      = EXCLUDED.solar_w_m2,
      ingested_at     = now()
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(sql, r)
        conn.commit()


def run() -> int:
    """Pull all active fetch stations + persist. Returns total rows."""
    run_time = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    total: list[dict] = []
    for code in ACTIVE_FETCH_STATIONS:
        s = STATIONS[code]
        try:
            rows = fetch_for_station(s, run_time=run_time)
        except Exception as exc:
            log.warning("atmos fetch failed for %s: %s", code, exc)
            continue
        log.info("atmos %s: %d rows", code, len(rows))
        total.extend(rows)
    if total:
        upsert(total)
    return len(total)


def latest_at_valid_time(station: str, valid_time) -> dict | None:
    """Return latest atmospheric signal row for (station, valid_time). Used by
    dashboard to show 'what does the model see right now for today's TMAX'."""
    sql = """
    SELECT * FROM atmosphere_signals
     WHERE station = %s AND valid_time = %s
     ORDER BY run_time DESC LIMIT 1
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, valid_time))
        return cur.fetchone()


def daily_features(station: str, target_date) -> dict | None:
    """Aggregate atmospheric signals across the local-day window for the given
    station and target_date. Returns peak BL height, mean cloud cover, peak solar,
    avg 850/925mb temps — useful for explaining why a TMAX forecast might
    over/undershoot.

    Local day uses STATIONS[station].tz so KMDW (CT), KDEN (MT), KLAX (PT)
    aggregate over the right window — DB-session-tz date would mis-bucket
    boundary hours."""
    from weather_bot.config import STATIONS
    tz = STATIONS[station].tz
    sql = """
    WITH latest_run AS (
        SELECT MAX(run_time) AS rt FROM atmosphere_signals
         WHERE station = %s AND (valid_time AT TIME ZONE %s)::date = %s
    )
    SELECT MAX(bl_height_m)     AS bl_peak_m,
           AVG(cloud_cover_pct) AS cloud_mean_pct,
           MAX(solar_w_m2)      AS solar_peak_w_m2,
           AVG(tmp_850_f)       AS tmp_850_mean_f,
           AVG(tmp_925_f)       AS tmp_925_mean_f,
           COUNT(*)             AS n_hours
      FROM atmosphere_signals, latest_run
     WHERE station = %s AND (valid_time AT TIME ZONE %s)::date = %s
       AND run_time = latest_run.rt
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, tz, target_date, station, tz, target_date))
        r = cur.fetchone()
    if not r or r["n_hours"] == 0:
        return None
    return {
        "bl_peak_m":       float(r["bl_peak_m"]) if r["bl_peak_m"] is not None else None,
        "cloud_mean_pct":  float(r["cloud_mean_pct"]) if r["cloud_mean_pct"] is not None else None,
        "solar_peak_w_m2": float(r["solar_peak_w_m2"]) if r["solar_peak_w_m2"] is not None else None,
        "tmp_850_mean_f":  float(r["tmp_850_mean_f"]) if r["tmp_850_mean_f"] is not None else None,
        "tmp_925_mean_f":  float(r["tmp_925_mean_f"]) if r["tmp_925_mean_f"] is not None else None,
        "n_hours":         int(r["n_hours"]),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = run()
    log.info("atmos: %d total rows persisted", n)
