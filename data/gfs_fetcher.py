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
from datetime import datetime

from weather_bot.config import ACTIVE_STATIONS, STATIONS, Station
from weather_bot.data import openmeteo_det_fetcher, persistence

log = logging.getLogger(__name__)

OM_GFS_URL = "https://api.open-meteo.com/v1/gfs"
HORIZON_DAYS = openmeteo_det_fetcher.HORIZON_DAYS


def _latest_gfs_cycle(now: datetime | None = None) -> datetime:
    """Most recent GFS run time (00/06/12/18 UTC)."""
    return openmeteo_det_fetcher.latest_six_hour_cycle(now)


def fetch_gfs_tmp_series(station: Station, horizon_days: int = HORIZON_DAYS,
                          run_time: datetime | None = None) -> list[dict]:
    """Pull hourly 2m temp from Open-Meteo's GFS, return det_forecast rows."""
    return openmeteo_det_fetcher.fetch_tmp_series(
        station=station,
        model="GFS",
        url=OM_GFS_URL,
        horizon_days=horizon_days,
        run_time=run_time or _latest_gfs_cycle(),
    )


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
