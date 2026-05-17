"""Backfill WeatherNext 2 ensemble cycles for calibration replay.

This is intentionally bounded because WeatherNext is queried through BigQuery.
Start with a short window and small horizon, then widen only if the first pass
is fast and affordable.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from weather_bot.config import ACTIVE_TRADE_STATIONS
from weather_bot.data import openmeteo_det_fetcher, weathernext_fetcher

log = logging.getLogger(__name__)


def parse_station_list(value: str) -> list[str]:
    return [station.strip().upper() for station in value.split(",") if station.strip()]


def historical_six_hour_cycles(
    *,
    days_back: int,
    now: datetime | None = None,
    include_latest: bool = True,
) -> list[datetime]:
    """Return descending 00/06/12/18 UTC cycles across a bounded lookback."""
    if days_back < 1:
        raise ValueError("days_back must be >= 1")
    latest = openmeteo_det_fetcher.latest_six_hour_cycle(now)
    count = days_back * 4
    start = 0 if include_latest else 1
    return [latest - timedelta(hours=6 * i) for i in range(start, start + count)]


def run(
    *,
    days_back: int = 7,
    stations: list[str] | None = None,
    horizon_days: int = 3,
    table: str | None = None,
    max_cycles: int | None = None,
    dry_run: bool = False,
) -> dict:
    stations = stations or ACTIVE_TRADE_STATIONS
    cycles = historical_six_hour_cycles(days_back=days_back)
    if max_cycles is not None:
        cycles = cycles[:max_cycles]

    total_rows = 0
    nonzero_cycles = 0
    failures = 0
    for cycle in cycles:
        log.info(
            "WeatherNext2 backfill cycle=%s stations=%s horizon_days=%s",
            cycle.isoformat(),
            ",".join(stations),
            horizon_days,
        )
        if dry_run:
            continue
        try:
            count = weathernext_fetcher.run(
                cycle=cycle,
                stations=stations,
                horizon_days=horizon_days,
                table=table,
            )
        except Exception as exc:
            failures += 1
            log.warning("WeatherNext2 backfill failed for cycle=%s: %s", cycle.isoformat(), exc)
            continue
        total_rows += count
        if count:
            nonzero_cycles += 1

    return {
        "cycles": len(cycles),
        "nonzero_cycles": nonzero_cycles,
        "failures": failures,
        "rows": total_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--stations", default=",".join(ACTIVE_TRADE_STATIONS),
                        help="Comma-separated station codes. Defaults to active trading stations.")
    parser.add_argument("--horizon-days", type=int, default=3,
                        help="Forecast horizon to persist per cycle. Keep small for first backfills.")
    parser.add_argument("--table", default=None,
                        help="Optional fully-qualified BigQuery table override.")
    parser.add_argument("--max-cycles", type=int, default=None,
                        help="Optional cap for smoke tests, e.g. 2.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print/log cycle plan without querying BigQuery.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = run(
        days_back=args.days_back,
        stations=parse_station_list(args.stations),
        horizon_days=args.horizon_days,
        table=args.table,
        max_cycles=args.max_cycles,
        dry_run=args.dry_run,
    )
    print(
        "WeatherNext2 backfill: "
        f"cycles={result['cycles']} nonzero_cycles={result['nonzero_cycles']} "
        f"failures={result['failures']} rows={result['rows']}"
    )


if __name__ == "__main__":
    main()
