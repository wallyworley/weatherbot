"""
HRRR (High-Resolution Rapid Refresh) fetcher.

HRRRv4 runs hourly at 3km CONUS resolution. We pull 2m TMP for each lead
hour that falls within the station-local calendar day(s) we care about,
then persist each hourly forecast. Downstream code computes daily Tmax by
max-reducing across hours.

HRRR short-range runs (non-00/06/12/18Z) go 18h out.
HRRR extended runs (00/06/12/18Z) go 48h out.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from weather_bot.config import ACTIVE_STATIONS, HRRR_BUCKET, HRRR_SELECTORS, STATIONS, Station
from weather_bot.data import grib_utils, persistence

log = logging.getLogger(__name__)


def latest_run_time(now: datetime | None = None) -> datetime:
    """Most recent hourly HRRR cycle that should be available (~1h after run)."""
    now = (now or datetime.now(tz=timezone.utc)) - timedelta(hours=1)
    return now.replace(minute=0, second=0, microsecond=0)


def _hrrr_key(run: datetime, lead_hr: int) -> str:
    return (
        f"hrrr.{run:%Y%m%d}/conus/"
        f"hrrr.t{run.hour:02d}z.wrfsfcf{lead_hr:02d}.grib2"
    )


def fetch_hrrr_tmp_series(run: datetime, station: Station, max_lead: int = 48) -> list[dict]:
    """Pull 2m TMP at the station grid point for each lead hour of this HRRR run."""
    rows: list[dict] = []
    is_extended = run.hour in (0, 6, 12, 18)
    horizon = max_lead if is_extended else 18

    for lead in range(1, horizon + 1):
        key = _hrrr_key(run, lead)
        if not grib_utils.object_exists(HRRR_BUCKET, key):
            continue
        try:
            idx = grib_utils.parse_idx(HRRR_BUCKET, key)
        except Exception as exc:
            log.warning("idx parse failed for %s: %s", key, exc)
            continue

        msgs = grib_utils.filter_messages(idx, HRRR_SELECTORS)
        if not msgs:
            continue
        raw = grib_utils.download_ranges(HRRR_BUCKET, key, msgs)
        tmp = grib_utils.save_temp(raw)
        try:
            ds = grib_utils.open_dataset(tmp)
            pt = grib_utils.nearest_point(ds, station.lat, station.lon)
            val_k = float(list(pt.data_vars.values())[0].values)
            rows.append(
                dict(
                    station=station.code,
                    model="HRRR",
                    run_time=run,
                    valid_time=run + timedelta(hours=lead),
                    lead_hr=lead,
                    var="TMP_2M",
                    value=grib_utils.kelvin_to_fahrenheit(val_k),
                )
            )
        finally:
            tmp.unlink(missing_ok=True)

    return rows


def run(cycle: datetime | None = None) -> None:
    cycle = cycle or latest_run_time()
    all_rows: list[dict] = []
    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        log.info("HRRR: station=%s cycle=%s", code, cycle)
        rows = fetch_hrrr_tmp_series(cycle, station)
        all_rows.extend(rows)
    if all_rows:
        persistence.upsert_det_forecast(all_rows, record_provenance=True)
        log.info("Persisted %d HRRR rows", len(all_rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
