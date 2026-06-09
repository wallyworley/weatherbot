"""
NBM (National Blend of Models) fetcher.

We care about two product families:
  - `core`  : deterministic mean, runs hourly, lead 1-264h.
  - `qmd`   : probabilistic percentiles (1/5/10/25/50/75/90/95/99),
              runs every 6 hours, lead 6-192h.

NBM QMD encodes daily extrema as PERIOD-AGGREGATED messages, NOT hourly
instantaneous temperatures. The message inventory looks like:

  lead 18: TMP:2 m above ground:0-18 hour min fcst:P% level
  lead 30: TMP:2 m above ground:12-30 hour max fcst:P% level
  lead 42: TMP:2 m above ground:24-42 hour min fcst:P% level
  lead 54: TMP:2 m above ground:36-54 hour max fcst:P% level
  ...

Each message is the min (or max) of 2m TMP over an 18-hour window ending
at the lead hour. Min and max alternate every 12h.

For station-local day D's daily TMAX, we pick the `:N-M hour max fcst:`
message whose [run+N, run+M] window contains the day's diurnal peak
(roughly 14:00 station-local). Same logic with `min fcst` for TMIN.

Earlier versions of this file scanned every hourly lead in the local-day
window and aggregated with max()/min(), which silently mislabeled overnight
TMIN data as daily TMAX whenever the run cycle put no max-fcst window
inside the local day (e.g., 00z runs for NYC: lead 30 max-fcst falls at
lead-30 = next-day 06z UTC, outside the local-day window of leads 4-27).
The new code addresses NBM's structure directly.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

import pytz

from weather_bot.config import (
    ACTIVE_STATIONS,
    NBM_BUCKET,
    NBM_PERCENTILES,
    STATIONS,
    Station,
)
from weather_bot.data import grib_utils, persistence

log = logging.getLogger(__name__)

# Substring used to find 2m TMP percentile messages. NBM QMD ships these
# in two formats per period:
#   - exceedance: ":TMP:...:N-M hour max fcst:prob <K:..."  (P(T<K kelvin))
#   - quantile:   ":TMP:...:N-M hour max fcst:P% level"     (value at percentile P)
# We use the quantile form. The `% level` filter in the parser distinguishes
# between the two; the `max fcst` / `min fcst` filter distinguishes TMAX vs TMIN.
_PCTL_SELECTOR = "TMP:2 m above ground"

# NBM QMD daily extrema windows are 18 hours wide and aligned to absolute
# UTC hours (NOT run-relative). Verified against 00z/06z/12z/18z runs of
# 2026-04-29 + 2026-04-30 against the public NBM bucket:
#
#   MIN window ends at 18:00 UTC of target_day  (covers prior-evening through morning)
#   MAX window ends at 06:00 UTC of target_day+1 (covers morning through evening)
#
# Both spans are 18 hours wide. For CONUS stations these windows contain
# the local diurnal extremes:
#   NYC (UTC-4): MIN window 20:00 prev day → 14:00 day_of  → contains overnight low
#                MAX window 08:00 day_of   → 02:00 next day → contains afternoon peak
#   LA  (UTC-7): MIN window 17:00 prev day → 11:00 day_of  → contains overnight low
#                MAX window 05:00 day_of   → 23:00 day_of   → contains afternoon peak
#
# QMD's smallest published lead for these aggregated families is 18h, and
# the upper horizon is ~192h.
_QMD_WINDOW_HOURS = 18
_QMD_MIN_HORIZON = 18
_QMD_MAX_HORIZON = 192
_QMD_MIN_WINDOW_END_UTC_HOUR = 18  # window ends 18:00 UTC on target_day
_QMD_MAX_WINDOW_END_UTC_HOUR = 6   # window ends 06:00 UTC on target_day + 1


def latest_run_time(now: datetime | None = None, cadence_hours: int = 6) -> datetime:
    """Return the most recent NBM QMD cycle that should be available.

    NBM QMD cycles publish roughly 2-3 hours after cycle time. We subtract 3h
    to be safe.
    """
    now = (now or datetime.now(tz=timezone.utc)) - timedelta(hours=3)
    hr = (now.hour // cadence_hours) * cadence_hours
    return now.replace(hour=hr, minute=0, second=0, microsecond=0)


def _nbm_key(run: datetime, product: str, lead_hr: int) -> str:
    return (
        f"blend.{run:%Y%m%d}/{run.hour:02d}/{product}/"
        f"blend.t{run.hour:02d}z.{product}.f{lead_hr:03d}.co.grib2"
    )


def _local_day_window_utc(station: Station, local_day: date) -> list[datetime]:
    """Return the list of UTC datetimes (hourly) covering the station-local day."""
    tz = pytz.timezone(station.tz)
    local_start = tz.localize(datetime.combine(local_day, datetime.min.time()))
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)
    hours = []
    t = utc_start
    while t < utc_end:
        hours.append(t)
        t += timedelta(hours=1)
    return hours


def _qmd_window_end_utc(target_day: date, fcst_type: str) -> datetime:
    """Absolute UTC datetime where the NBM QMD window ENDS for the given
    target_day. NBM aligns these to fixed UTC hours regardless of run cycle:
    MIN windows end at 18:00 UTC of target_day, MAX windows end at 06:00 UTC
    of target_day + 1.
    """
    if fcst_type == "max":
        end_day = target_day + timedelta(days=1)
        end_hour = _QMD_MAX_WINDOW_END_UTC_HOUR
    else:
        end_day = target_day
        end_hour = _QMD_MIN_WINDOW_END_UTC_HOUR
    return datetime.combine(end_day, time(hour=end_hour), tzinfo=timezone.utc)


def _find_period_message_lead(
    run: datetime,
    target_day: date,
    fcst_type: str,
) -> int | None:
    """Compute the lead hour from `run` to the END of the NBM QMD window
    for `target_day`'s daily extreme of type `fcst_type` ('max' or 'min').

    Returns None if the lead falls outside QMD's published horizon (1-192h)
    or before the smallest aggregation lead (18h).
    """
    window_end_utc = _qmd_window_end_utc(target_day, fcst_type)
    lead = int(round((window_end_utc - run).total_seconds() / 3600.0))
    if lead < _QMD_MIN_HORIZON or lead > _QMD_MAX_HORIZON:
        return None
    return lead


def _read_percentiles_at_lead(
    run: datetime,
    lead: int,
    station: Station,
    fcst_keyword: str,
) -> dict[int, float] | None:
    """Open the QMD file at `lead` and extract {percentile: temp_f} for the
    `fcst_keyword`-bearing message family ('max fcst' or 'min fcst').
    """
    key = _nbm_key(run, "qmd", lead)
    if not grib_utils.object_exists(NBM_BUCKET, key):
        log.warning("NBM QMD missing: %s", key)
        return None
    try:
        idx = grib_utils.parse_idx(NBM_BUCKET, key)
    except Exception as exc:
        log.warning("idx parse failed for %s: %s", key, exc)
        return None

    msgs_by_pct: dict[int, grib_utils.GribMessage] = {}
    for m in idx:
        if _PCTL_SELECTOR not in m.line:
            continue
        if fcst_keyword not in m.line:
            continue
        for part in m.line.split(":"):
            if part.endswith("% level"):
                try:
                    pct = int(part.replace("% level", "").strip())
                except ValueError:
                    continue
                if pct in NBM_PERCENTILES:
                    msgs_by_pct[pct] = m

    if not msgs_by_pct:
        log.warning("No '%s' percentile msgs at lead %d for %s", fcst_keyword, lead, key)
        return None

    per_pct: dict[int, float] = {}
    for pct, msg in msgs_by_pct.items():
        single = grib_utils.save_temp(
            grib_utils.download_ranges(NBM_BUCKET, key, [msg])
        )
        try:
            ds_one = grib_utils.open_dataset(single)
            pt = grib_utils.nearest_point(ds_one, station.lat, station.lon)
            val_k = float(list(pt.data_vars.values())[0].values)
            per_pct[pct] = grib_utils.kelvin_to_fahrenheit(val_k)
        finally:
            single.unlink(missing_ok=True)
    return per_pct


def fetch_nbm_qmd_daily_percentiles(
    run: datetime,
    station: Station,
    target_day: date,
    var: str,
) -> dict[int, float] | None:
    """Return {percentile: temp_f} for the daily TMAX or TMIN of `target_day`.

    Picks the QMD message family whose 18-hour aggregation window contains
    the target day's diurnal peak (TMAX) or trough (TMIN) in station-local
    time. Returns None if the required message isn't published in this run,
    or if its window doesn't actually overlap `target_day` (sanity guard
    against future NBM schedule changes).
    """
    if var == "TMAX_DAILY":
        fcst_type, fcst_keyword = "max", "max fcst"
    elif var == "TMIN_DAILY":
        fcst_type, fcst_keyword = "min", "min fcst"
    else:
        raise ValueError(f"Unsupported var: {var}")

    lead = _find_period_message_lead(run, target_day, fcst_type)
    if lead is None:
        log.warning(
            "No %s lead in QMD horizon for %s %s from run %s",
            fcst_keyword, station.code, target_day, run,
        )
        return None

    # Sanity guard: confirm the chosen window overlaps the target day's
    # local-time window. If NBM ever shifts its alignment, this catches
    # the regression instead of silently mislabeling the data.
    window_start = run + timedelta(hours=lead - _QMD_WINDOW_HOURS)
    window_end = run + timedelta(hours=lead)
    day_hours_utc = _local_day_window_utc(station, target_day)
    day_start, day_end = day_hours_utc[0], day_hours_utc[-1] + timedelta(hours=1)
    if not (window_end > day_start and window_start < day_end):
        log.error(
            "Chosen %s window [%s, %s] does not overlap target day [%s, %s] for %s",
            fcst_keyword, window_start, window_end, day_start, day_end, station.code,
        )
        return None

    return _read_percentiles_at_lead(run, lead, station, fcst_keyword)


# ---------------------------------------------------------------------------
# Fetch-once / extract-all-stations fast path (backfill optimization).
#
# The per-station path above re-downloads and re-decodes the SAME grib message
# once per station (the lead — and therefore the message — is station-
# independent). For N stations that is N× redundant cfgrib decoding. The
# functions below decode each percentile message ONCE and extract every
# station's nearest grid point from the shared dataset. Output is identical
# per-station to looping fetch_nbm_qmd_daily_percentiles (proven by
# research/validate_fast_nbm.py). Live code paths are untouched.
# ---------------------------------------------------------------------------
def _station_window_ok(run: datetime, lead: int, station: Station, target_day: date) -> bool:
    """Same sanity guard as fetch_nbm_qmd_daily_percentiles: does the chosen
    QMD window overlap the station's local target day?"""
    window_start = run + timedelta(hours=lead - _QMD_WINDOW_HOURS)
    window_end = run + timedelta(hours=lead)
    day_hours_utc = _local_day_window_utc(station, target_day)
    day_start, day_end = day_hours_utc[0], day_hours_utc[-1] + timedelta(hours=1)
    return window_end > day_start and window_start < day_end


def _find_qmd_pct_messages(run: datetime, lead: int, fcst_keyword: str):
    """Return (key, {percentile: GribMessage}) for the QMD file at `lead`.
    Mirrors the message-selection logic in _read_percentiles_at_lead."""
    key = _nbm_key(run, "qmd", lead)
    if not grib_utils.object_exists(NBM_BUCKET, key):
        log.warning("NBM QMD missing: %s", key)
        return key, {}
    try:
        idx = grib_utils.parse_idx(NBM_BUCKET, key)
    except Exception as exc:
        log.warning("idx parse failed for %s: %s", key, exc)
        return key, {}
    msgs_by_pct: dict[int, grib_utils.GribMessage] = {}
    for m in idx:
        if _PCTL_SELECTOR not in m.line or fcst_keyword not in m.line:
            continue
        for part in m.line.split(":"):
            if part.endswith("% level"):
                try:
                    pct = int(part.replace("% level", "").strip())
                except ValueError:
                    continue
                if pct in NBM_PERCENTILES:
                    msgs_by_pct[pct] = m
    return key, msgs_by_pct


def fetch_qmd_all_stations(
    run: datetime,
    target_day: date,
    var: str,
    stations: list[Station],
) -> dict[str, dict[int, float]]:
    """Fetch-once variant of fetch_nbm_qmd_daily_percentiles for many stations.

    Decodes each percentile message ONCE and extracts every eligible station's
    nearest grid point. Returns {station_code: {percentile: temp_f}} containing
    only stations that pass the window guard and have data. Per-station values
    are identical to fetch_nbm_qmd_daily_percentiles (same nearest_point + unit
    conversion on the same decoded grid)."""
    if var == "TMAX_DAILY":
        fcst_type, fcst_keyword = "max", "max fcst"
    elif var == "TMIN_DAILY":
        fcst_type, fcst_keyword = "min", "min fcst"
    else:
        raise ValueError(f"Unsupported var: {var}")

    lead = _find_period_message_lead(run, target_day, fcst_type)
    if lead is None:
        return {}
    eligible = [s for s in stations if _station_window_ok(run, lead, s, target_day)]
    if not eligible:
        return {}
    key, msgs_by_pct = _find_qmd_pct_messages(run, lead, fcst_keyword)
    if not msgs_by_pct:
        return {}

    out: dict[str, dict[int, float]] = {s.code: {} for s in eligible}
    for pct, msg in msgs_by_pct.items():
        single = grib_utils.save_temp(grib_utils.download_ranges(NBM_BUCKET, key, [msg]))
        try:
            ds_one = grib_utils.open_dataset(single)
            for s in eligible:
                pt = grib_utils.nearest_point(ds_one, s.lat, s.lon)
                val_k = float(list(pt.data_vars.values())[0].values)
                out[s.code][pct] = grib_utils.kelvin_to_fahrenheit(val_k)
        finally:
            single.unlink(missing_ok=True)
    return {code: pcts for code, pcts in out.items() if pcts}


def run_fast(
    target_days: Iterable[date] | None = None,
    cycle: datetime | None = None,
    max_fallback_cycles: int = 4,
) -> None:
    """Backfill-optimized run(): identical output to run(), but each grib
    message is decoded once for all stations instead of once per station.

    Per-station 6-hour cycle fallback is preserved: each station independently
    settles on the most recent cycle that yields its data."""
    first_cycle = cycle or latest_run_time()
    today = datetime.now(tz=timezone.utc).date()
    if target_days is None:
        target_days = [today + timedelta(days=i) for i in range(8)]
    target_days = list(target_days)
    stations = [STATIONS[c] for c in ACTIVE_STATIONS]

    all_rows: list[dict] = []
    for d in target_days:
        for var in ("TMAX_DAILY", "TMIN_DAILY"):
            remaining = {s.code: s for s in stations}
            results: dict[str, dict[int, float]] = {}
            cycle_used: dict[str, datetime] = {}
            tried_cycle = first_cycle
            for _ in range(max_fallback_cycles + 1):
                if not remaining:
                    break
                got = fetch_qmd_all_stations(tried_cycle, d, var, list(remaining.values()))
                for code, pcts in got.items():
                    if pcts:
                        results[code] = pcts
                        cycle_used[code] = tried_cycle
                        remaining.pop(code, None)
                if not remaining:
                    break
                tried_cycle = tried_cycle - timedelta(hours=6)
            for code, pcts in results.items():
                for p, v in pcts.items():
                    all_rows.append(dict(
                        station=code, model="NBM_QMD", run_time=cycle_used[code],
                        valid_date=d, var=var, percentile=p, value=v,
                    ))
    if all_rows:
        persistence.upsert_prob_forecast(all_rows, record_provenance=False)
        log.info("Persisted %d NBM QMD rows (fast path)", len(all_rows))
    else:
        log.warning("No NBM QMD rows persisted across any cycle (fast path)")


def run(
    target_days: Iterable[date] | None = None,
    cycle: datetime | None = None,
    max_fallback_cycles: int = 4,
) -> None:
    """Main entrypoint: pull latest NBM QMD percentiles for each active station & target day.

    NBM QMD files publish a few hours after cycle time; occasionally a cycle
    is delayed or incomplete. If the selected cycle returns no data for a
    (station, day) pair, fall back to the previous 6-hour cycle and retry,
    up to `max_fallback_cycles` older cycles (= 24h look-back by default).
    """
    first_cycle = cycle or latest_run_time()
    today = datetime.now(tz=timezone.utc).date()
    if target_days is None:
        target_days = [today + timedelta(days=i) for i in range(8)]
    target_days = list(target_days)

    all_rows: list[dict] = []
    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        for d in target_days:
            # Each var is fetched independently — TMAX and TMIN come from
            # different message families that may live at different leads
            # (and thus might fall back differently across cycles).
            for var in ("TMAX_DAILY", "TMIN_DAILY"):
                pcts: dict[int, float] | None = None
                tried_cycle = first_cycle
                for fallback_step in range(max_fallback_cycles + 1):
                    log.info(
                        "NBM QMD: station=%s target=%s var=%s cycle=%s",
                        code, d, var, tried_cycle,
                    )
                    pcts = fetch_nbm_qmd_daily_percentiles(tried_cycle, station, d, var)
                    if pcts:
                        break
                    log.warning(
                        "No NBM QMD %s for %s %s at cycle %s — falling back 6h",
                        var, code, d, tried_cycle,
                    )
                    tried_cycle = tried_cycle - timedelta(hours=6)
                if not pcts:
                    log.error(
                        "Exhausted %d fallback cycles for %s %s %s — no NBM data",
                        max_fallback_cycles, code, d, var,
                    )
                    continue
                for p, v in pcts.items():
                    all_rows.append(
                        dict(
                            station=code,
                            model="NBM_QMD",
                            run_time=tried_cycle,
                            valid_date=d,
                            var=var,
                            percentile=p,
                            value=v,
                        )
                    )
    if all_rows:
        persistence.upsert_prob_forecast(all_rows, record_provenance=True)
        log.info("Persisted %d NBM QMD rows", len(all_rows))
    else:
        log.warning("No NBM QMD rows persisted across any cycle")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
