"""ECMWF hourly forecast fetcher (via Open-Meteo).

Open-Meteo now exposes ECMWF IFS through `/v1/ecmwf`, including hourly
temperature series that fit the same `det_forecast` contract as HRRR/GFS.
This is the lowest-friction way to benchmark ECMWF in the bot before allowing
it to influence trading probabilities.
"""
from __future__ import annotations

import logging
from datetime import datetime

from weather_bot.config import ACTIVE_STATIONS, STATIONS, Station
from weather_bot.data import openmeteo_det_fetcher, persistence

log = logging.getLogger(__name__)

OM_ECMWF_URL = "https://api.open-meteo.com/v1/ecmwf"
HORIZON_DAYS = openmeteo_det_fetcher.HORIZON_DAYS


def _latest_ecmwf_cycle(now: datetime | None = None) -> datetime:
    """Most recent ECMWF run time exposed on a 6-hour cadence."""
    return openmeteo_det_fetcher.latest_six_hour_cycle(now)


def fetch_ecmwf_tmp_series(
    station: Station,
    horizon_days: int = HORIZON_DAYS,
    run_time: datetime | None = None,
) -> list[dict]:
    """Pull hourly 2m temp from Open-Meteo's ECMWF endpoint."""
    return openmeteo_det_fetcher.fetch_tmp_series(
        station=station,
        model="ECMWF",
        url=OM_ECMWF_URL,
        horizon_days=horizon_days,
        run_time=run_time or _latest_ecmwf_cycle(),
    )


def run(cycle: datetime | None = None) -> None:
    cycle = cycle or _latest_ecmwf_cycle()
    all_rows: list[dict] = []
    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        log.info("ECMWF: station=%s cycle=%s", code, cycle)
        try:
            rows = fetch_ecmwf_tmp_series(station, run_time=cycle)
        except Exception as exc:
            log.warning("ECMWF fetch failed for %s: %s", code, exc)
            continue
        all_rows.extend(rows)
    if all_rows:
        persistence.upsert_det_forecast(all_rows)
        log.info("Persisted %d ECMWF rows", len(all_rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
