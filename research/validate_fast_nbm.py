"""
Prove nbm_fetcher.fetch_qmd_all_stations produces byte-identical per-station
values to looping the live fetch_nbm_qmd_daily_percentiles.

Runs both paths over every ACTIVE station for a sample (cycle, day, var) set
and asserts every (station, percentile) value matches exactly. Exits non-zero
on any mismatch so it can gate the backfill swap.

Usage:
    python -m weather_bot.research.validate_fast_nbm
    python -m weather_bot.research.validate_fast_nbm --date 2023-05-15
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone

from weather_bot.config import ACTIVE_STATIONS, STATIONS
from weather_bot.data import nbm_fetcher as nf

log = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2023-05-15", help="target day (YYYY-MM-DD)")
    args = ap.parse_args()
    d = date.fromisoformat(args.date)
    cycle = datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)
    stations = [STATIONS[c] for c in ACTIVE_STATIONS]

    mismatches = 0
    checked = 0
    for target in (d, d + timedelta(days=1)):
        for var in ("TMAX_DAILY", "TMIN_DAILY"):
            # FAST path: one decode per percentile, all stations
            fast = nf.fetch_qmd_all_stations(cycle, target, var, stations)
            # SLOW path: the live per-station function, looped
            for s in stations:
                slow = nf.fetch_nbm_qmd_daily_percentiles(cycle, s, target, var)
                f = fast.get(s.code)
                if not slow and not f:
                    continue
                if (slow or {}).keys() != (f or {}).keys():
                    log.error("KEY mismatch %s %s %s: slow=%s fast=%s",
                              s.code, target, var, slow, f)
                    mismatches += 1
                    continue
                for pct, sv in (slow or {}).items():
                    fv = f[pct]
                    checked += 1
                    # exact compare; NaN==NaN handled explicitly
                    if sv != fv and not (sv != sv and fv != fv):
                        log.error("VALUE mismatch %s %s %s p%d: slow=%.6f fast=%.6f",
                                  s.code, target, var, pct, sv, fv)
                        mismatches += 1
    print(f"checked {checked} (station,percentile) values; mismatches={mismatches}")
    if mismatches:
        print("FAIL — fast path differs from live path; do NOT swap")
        return 1
    print("PASS — fast path is identical to the live per-station path")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(main())
