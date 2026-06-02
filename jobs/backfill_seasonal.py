"""
Targeted multi-year *seasonal* backfill of NBM-QMD forecasts + daily obs.

Unlike `backfill_history` (which pulls a contiguous N-days-back-from-now block),
this fills only the specified (year, month) windows. That lets us thicken the
month-keyed `station_bias` cells with prior-year same-season data — e.g. build
real June bias from June 2024+2025, and thicken May — without downloading the
two years of intervening days in between.

For each day D in each window it pulls the 12Z NBM-QMD cycle forecasting D and
D+1 (lead 0 and lead 1) into `prob_forecast`, and aggregates IEM METAR/HFMETAR
into `daily_obs`. Raw 5-min obs are NOT stored (we only need the daily Tmax/Tmin
for bias training), keeping `metar_obs` from bloating. Run `retrain_bias_*`
afterwards to fold the new pairs into the bias tables.

Usage:
    python -m weather_bot.jobs.backfill_seasonal --years 2023,2024,2025 --months 4,5,6
    python -m weather_bot.jobs.backfill_seasonal --years 2025 --months 6 --dry-run
"""
from __future__ import annotations

import argparse
import calendar
import logging
from datetime import date, datetime, timedelta, timezone

from weather_bot.config import ACTIVE_STATIONS, STATIONS
from weather_bot.data import iem_fetcher, metar_fetcher, nbm_fetcher, persistence

log = logging.getLogger(__name__)


def _obs_for_window(start: date, end: date, dry_run: bool) -> int:
    """Aggregate IEM obs -> daily_obs for [start, end). Returns daily rows."""
    total = 0
    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        try:
            if station.is_asos:
                metars = iem_fetcher.fetch_historical_5min(code, start, end)
                tag = "HFMETAR"
            else:
                metars = iem_fetcher.fetch_historical(code, start, end)
                tag = "METAR"
        except Exception as exc:
            log.error("obs fetch failed %s %s..%s: %s", code, start, end, exc)
            continue
        metars = metar_fetcher.filter_implausible_swings(metars)
        daily_rows: list[dict] = []
        d = start
        while d < end:
            row = metar_fetcher.compute_daily(station, metars, d)
            if row:
                row["source"] = tag
                daily_rows.append(row)
            d += timedelta(days=1)
        if daily_rows and not dry_run:
            persistence.upsert_daily_obs(daily_rows)
        total += len(daily_rows)
        log.info("obs %s %s..%s src=%s -> %d daily rows%s",
                 code, start, end, tag, len(daily_rows), " (dry)" if dry_run else "")
    return total


def _nbm_for_window(start: date, end: date, dry_run: bool) -> int:
    """Pull the 12Z NBM-QMD cycle for each day D forecasting D and D+1."""
    days = 0
    d = start
    while d < end:
        cycle = datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)
        if dry_run:
            log.info("NBM (dry) cycle=%s target=[%s,%s]", cycle, d, d + timedelta(days=1))
        else:
            try:
                # Fast path: decode each grib message once for all stations
                # (identical output to run(), ~Nx fewer cfgrib decodes).
                nbm_fetcher.run_fast(target_days=[d, d + timedelta(days=1)], cycle=cycle)
            except Exception as exc:
                log.warning("NBM backfill failed for %s: %s", d, exc)
        days += 1
        d += timedelta(days=1)
    return days


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True, help="comma list, e.g. 2023,2024,2025")
    ap.add_argument("--months", required=True, help="comma list, e.g. 4,5,6")
    ap.add_argument("--dry-run", action="store_true")
    # NBM (S3) tolerates heavy parallelism; obs (IEM) rate-limits, so the two
    # are split into separate passes with different concurrency. These flags
    # let one invocation do only one side.
    ap.add_argument("--skip-obs", action="store_true", help="NBM-only pass")
    ap.add_argument("--skip-nbm", action="store_true", help="obs-only pass")
    args = ap.parse_args()

    years = [int(y) for y in args.years.split(",") if y.strip()]
    months = [int(m) for m in args.months.split(",") if m.strip()]
    today = datetime.now(tz=timezone.utc).date()

    windows: list[tuple[date, date]] = []
    for y in years:
        for m in months:
            start = date(y, m, 1)
            end = date(y, m, calendar.monthrange(y, m)[1]) + timedelta(days=1)
            if start >= today:
                continue              # wholly in the future
            end = min(end, today)     # don't fetch days that haven't happened
            windows.append((start, end))

    log.info("Seasonal backfill: %d windows across years=%s months=%s%s",
             len(windows), years, months, " (DRY RUN)" if args.dry_run else "")
    for start, end in windows:
        log.info("=== window %s .. %s (%d days, %d stations) ===",
                 start, end, (end - start).days, len(ACTIVE_STATIONS))
        n_obs = 0 if args.skip_obs else _obs_for_window(start, end, args.dry_run)
        n_days = 0 if args.skip_nbm else _nbm_for_window(start, end, args.dry_run)
        log.info("--- window %s done: %d daily-obs rows, %d NBM days ---",
                 start, n_obs, n_days)
    log.info("Seasonal backfill complete. Run retrain_bias_station_aware next.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
