"""
Station bias correction.

We compute rolling 30-day bias per (station, model, variable, month, lead_day):
    mean_bias_f = mean(forecast - observation)
    stddev_f    = std(forecast - observation)

At prediction time, apply:
    corrected_value = raw_value - mean_bias_f
    inflated_var    = model_var + stddev_f^2    (conservative)

Why month: temperature bias has a clear seasonal signature (radiative vs.
advective regimes, sun angle, boundary-layer behavior).

Why lead_day: short-range HRRR and long-range NBM have very different error
characteristics, and bias grows with lead time.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import numpy as np

from weather_bot.config import ACTIVE_STATIONS, STATIONS
from weather_bot.data import persistence

log = logging.getLogger(__name__)

ROLLING_WINDOW_DAYS = 30


def _collect_pairs(
    station: str,
    model: str,
    var: str,
    end_date: date,
    window: int = ROLLING_WINDOW_DAYS,
) -> list[tuple[int, int, float, float]]:
    """Return (month, lead_day, forecast, observation) tuples."""
    from weather_bot.data.persistence import connect

    start = end_date - timedelta(days=window)
    sql = """
    WITH obs AS (
        SELECT local_date, CASE WHEN %(var)s = 'TMAX_DAILY' THEN tmax_f ELSE tmin_f END AS obs
          FROM daily_obs
         WHERE station = %(station)s
           AND local_date BETWEEN %(start)s AND %(end)s
    ),
    fc AS (
        SELECT valid_date, run_time,
               -- Use median (50th percentile) as point estimate for bias
               MAX(value) FILTER (WHERE percentile = 50) AS fc50,
               (valid_date - run_time::date) AS lead_day
          FROM prob_forecast
         WHERE station = %(station)s AND model = %(model)s AND var = %(var)s
           AND valid_date BETWEEN %(start)s AND %(end)s
         GROUP BY valid_date, run_time
    )
    SELECT EXTRACT(MONTH FROM fc.valid_date)::int AS month,
           fc.lead_day::int,
           fc.fc50,
           obs.obs
      FROM fc JOIN obs ON fc.valid_date = obs.local_date
     WHERE fc.fc50 IS NOT NULL AND obs.obs IS NOT NULL
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, dict(station=station, model=model, var=var, start=start, end=end_date))
        return [(r["month"], r["lead_day"], r["fc50"], r["obs"]) for r in cur.fetchall()]


def _collect_det_pairs(
    station: str,
    model: str,
    var: str,
    end_date: date,
    window: int = ROLLING_WINDOW_DAYS,
) -> list[tuple[int, int, float, float]]:
    """Same but from det_forecast — reduces hourly TMP_2M to daily Tmax/Tmin first."""
    from weather_bot.data.persistence import connect

    agg = "MAX" if var == "TMAX_DAILY" else "MIN"
    start = end_date - timedelta(days=window)
    # Aggregate det_forecast to a daily value over the *station-local* day,
    # not the DB session timezone day. Otherwise KMDW (CT), KDEN (MT), KLAX
    # (PT) etc. would mis-bucket boundary-hour temperatures into the wrong day.
    tz = STATIONS[station].tz
    sql = f"""
    WITH obs AS (
        SELECT local_date, CASE WHEN %(var)s = 'TMAX_DAILY' THEN tmax_f ELSE tmin_f END AS obs
          FROM daily_obs
         WHERE station = %(station)s
           AND local_date BETWEEN %(start)s AND %(end)s
    ),
    fc AS (
        SELECT (valid_time AT TIME ZONE %(tz)s)::date AS valid_date,
               run_time,
               {agg}(value) AS fc_value,
               ((valid_time AT TIME ZONE %(tz)s)::date
                  - (run_time AT TIME ZONE %(tz)s)::date) AS lead_day
          FROM det_forecast
         WHERE station = %(station)s AND model = %(model)s AND var = 'TMP_2M'
           AND (valid_time AT TIME ZONE %(tz)s)::date BETWEEN %(start)s AND %(end)s
         GROUP BY (valid_time AT TIME ZONE %(tz)s)::date, run_time
    )
    SELECT EXTRACT(MONTH FROM fc.valid_date)::int AS month,
           fc.lead_day::int,
           fc.fc_value,
           obs.obs
      FROM fc JOIN obs ON fc.valid_date = obs.local_date
     WHERE fc.fc_value IS NOT NULL AND obs.obs IS NOT NULL
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, dict(station=station, model=model, var=var,
                                start=start, end=end_date, tz=tz))
        return [(r["month"], r["lead_day"], r["fc_value"], r["obs"]) for r in cur.fetchall()]


def recompute(end_date: date | None = None) -> None:
    """Compatibility wrapper for the supported bias retraining job.

    Historical callers used this module-level entrypoint, but its old SQL used
    UTC-date lead arithmetic and could overwrite the point-in-time-safe rows
    from `jobs.retrain_bias`. Keep the function so old cron/manual commands
    do not crash, but delegate all writes to the canonical retrainer.
    """
    if end_date is not None:
        log.warning(
            "bias_correction.recompute(end_date=...) is deprecated; "
            "end_date is ignored by jobs.retrain_bias"
        )
    from weather_bot.jobs.retrain_bias import retrain

    retrain()


_MIN_SAMPLE_SIZE_FOR_TRADING = 10
_MAX_BIAS_AGE_HOURS = 48


def is_station_calibrated(
    station: str,
    var: str,
    target_date: date,
    lead_day: int,
    min_n: int = _MIN_SAMPLE_SIZE_FOR_TRADING,
    max_age_hours: float = _MAX_BIAS_AGE_HOURS,
) -> tuple[bool, str | None]:
    """Pre-trade safety gate: is this (station, var, month, lead_day) cell
    fresh and well-sampled enough to trade?

    Returns (eligible, skip_reason). When skip_reason is set, main.py should
    force the signal to SKIP regardless of computed edge. This is the
    safety rail that lets us add new fetch-only stations without those
    stations accidentally trading uncalibrated.

    Important: get_station_bias has a month-fallback that returns April rows
    when May is thin. We must check the exact-month cell first — if it exists
    but has n < min_n, block the trade. Only treat a missing exact-month row
    as a pass-through to the fallback when the month literally has no data yet
    (e.g., the first day of a new month before any retrain).
    """
    month = target_date.month
    lead = max(lead_day, 0)

    # Exact-month check: if a cell exists for this month, its sample size rules.
    # The month-fallback in get_station_bias would hide a thin-but-present cell.
    exact = persistence.get_station_bias_exact(station, "NBM_QMD", var, month, lead)
    if exact is not None:
        n_exact = int(exact.get("sample_size") or 0)
        if n_exact < min_n:
            return False, f"BIAS_THIN|n={n_exact}<{min_n}|month={month}"
        # Exact month cell is thick enough — fall through to staleness check below.
        row = exact
    else:
        # No exact-month row yet (new month, first day). Use fallback so we
        # don't block trading entirely, but require the fallback to be thick.
        row = persistence.get_station_bias(station, "NBM_QMD", var, month, lead)
        if row is None:
            return False, f"BIAS_MISSING|station={station}|month={month}|lead={lead_day}"
        n = int(row.get("sample_size") or 0)
        if n < min_n:
            return False, f"BIAS_THIN|n={n}<{min_n}"

    updated_at = row.get("updated_at")
    if updated_at is not None:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(tz=timezone.utc) - updated_at).total_seconds() / 3600.0
        if age_h > max_age_hours:
            return False, f"BIAS_STALE|age={age_h:.1f}h>{max_age_hours}h"

    return True, None


def apply_bias(
    raw_value: float,
    station: str,
    model: str,
    var: str,
    month: int,
    lead_day: int,
    fallback_bias: float = 0.0,
) -> float:
    row = persistence.get_station_bias(station, model, var, month, lead_day)
    bias = row["mean_bias_f"] if row else fallback_bias
    return raw_value - bias


def bias_variance(
    station: str,
    model: str,
    var: str,
    month: int,
    lead_day: int,
    fallback_var: float = 4.0,
) -> float:
    row = persistence.get_station_bias(station, model, var, month, lead_day)
    if row:
        return float(row["stddev_f"]) ** 2
    return fallback_var


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    recompute()
