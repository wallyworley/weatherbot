"""Daily accumulator — appends a longitudinal snapshot to research/reports/.

Captures, for each fetch station:
- Yesterday's CLI TMAX/TMIN (ground truth) — falls back to IEM if NWS API is empty
- Tomorrow's ECMWF + GFS TMAX forecast (for next-day evaluation)

Runs once per day, ideally after the morning CLI has been issued (~8 AM ET).
Appends to `research/reports/longitudinal.csv`, idempotent on
(data_date, station) so re-runs don't duplicate. The accumulated CSV is what
the comparison scripts consume going forward — IEM stays as fallback for
backfill but day-to-day operation reads from this file.

Schedule via launchd / cron:
    30 13 * * *  cd /path/to/weatherbot && .venv/bin/python -m research.daily_snapshot
(13:30 UTC = 9:30 AM ET; safely after the morning CLI window.)
"""
from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from weather_bot.config import ACTIVE_FETCH_STATIONS

from research.sources.nws_text_products import (
    STATION_TO_LOC, fetch_text_iem, get_product, list_products, parse_cli_yesterday, parse_dsm,
)
from research.sources.openmeteo_fetcher import fetch_forecast_daily

log = logging.getLogger(__name__)

LONG_CSV = Path("research/reports/longitudinal.csv")


@dataclass
class SnapshotRow:
    captured_at: str            # ISO UTC timestamp of when row was written
    data_date: str              # local date the row's tmax/tmin describe
    station: str
    source: str                 # 'CLI' | 'DSM' | 'ECMWF' | 'GFS'
    tmax_f: Optional[float]
    tmin_f: Optional[float]
    note: str = ""


def _truth_for(target: date, station: str) -> tuple[Optional[float], Optional[float], str]:
    """CLI tmax/tmin for `target` data date, NWS API → IEM fallback."""
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None, None, "no_location_mapping"
    # NWS API
    try:
        start = datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(hours=18)
        products = list_products("CLI", loc, start=start, end=end, limit=10)
        if products:
            prod = get_product(products[0]["id"])
            obs = parse_cli_yesterday(prod.text)
            if obs.tmax_f is not None:
                return obs.tmax_f, obs.tmin_f, "nws_api"
    except Exception as e:
        log.warning("NWS CLI %s %s failed: %s", station, target, e)
    # IEM
    text = fetch_text_iem("CLI", station, target)
    if text:
        obs = parse_cli_yesterday(text)
        if obs.tmax_f is not None:
            return obs.tmax_f, obs.tmin_f, "iem"
    return None, None, "no_data"


def _dsm_for(target: date, station: str) -> tuple[Optional[float], Optional[float], str]:
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None, None, "no_location_mapping"
    try:
        start = datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(hours=18)
        products = list_products("DSM", loc, start=start, end=end, limit=10)
        if products:
            prod = get_product(products[0]["id"])
            obs = parse_dsm(prod.text)
            if obs.tmax_f is not None:
                return obs.tmax_f, obs.tmin_f, "nws_api"
    except Exception as e:
        log.warning("NWS DSM %s %s failed: %s", station, target, e)
    text = fetch_text_iem("DSM", station, target)
    if text:
        obs = parse_dsm(text)
        if obs.tmax_f is not None:
            return obs.tmax_f, obs.tmin_f, "iem"
    return None, None, "no_data"


def _existing_keys(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    keys = set()
    with path.open() as f:
        for r in csv.DictReader(f):
            keys.add((r["data_date"], r["station"], r["source"]))
    return keys


def _append_rows(path: Path, rows: list[SnapshotRow]) -> int:
    """Append rows whose (data_date, station, source) tuple isn't already on disk."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_keys(path)
    new = [r for r in rows if (r.data_date, r.station, r.source) not in existing]
    if not new:
        return 0
    write_header = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(new[0]).keys()))
        if write_header:
            w.writeheader()
        for r in new:
            w.writerow(asdict(r))
    return len(new)


def run(stations: list[str], today: Optional[date] = None) -> int:
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    captured = datetime.now(tz=timezone.utc).isoformat()
    rows: list[SnapshotRow] = []

    for st in stations:
        # Truth for yesterday (CLI + DSM)
        tmax, tmin, src_note = _truth_for(yesterday, st)
        rows.append(SnapshotRow(captured, yesterday.isoformat(), st, "CLI",
                                  tmax, tmin, note=src_note))
        dtmax, dtmin, dnote = _dsm_for(yesterday, st)
        rows.append(SnapshotRow(captured, yesterday.isoformat(), st, "DSM",
                                  dtmax, dtmin, note=dnote))
        # Forecast for tomorrow (ECMWF + GFS, current — not historical)
        for model in ("ecmwf", "gfs"):
            try:
                fc = fetch_forecast_daily(model, st, tomorrow, historical=False)
                rows.append(SnapshotRow(captured, tomorrow.isoformat(), st, model.upper(),
                                          fc.get("tmax_f"), fc.get("tmin_f"),
                                          note="open_meteo_current"))
            except Exception as e:
                log.warning("forecast %s %s %s: %s", model, st, tomorrow, e)
                rows.append(SnapshotRow(captured, tomorrow.isoformat(), st, model.upper(),
                                          None, None, note=f"err:{e}"))

    n_appended = _append_rows(LONG_CSV, rows)
    log.info("daily_snapshot: %d new rows appended to %s (%d candidates)", n_appended, LONG_CSV, len(rows))
    return n_appended


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", nargs="+", default=ACTIVE_FETCH_STATIONS)
    ap.add_argument("--today", default=None, help="Override today's date (YYYY-MM-DD), for backfill")
    args = ap.parse_args()
    today = date.fromisoformat(args.today) if args.today else None
    run(args.stations, today=today)
