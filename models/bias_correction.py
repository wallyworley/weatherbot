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
    sql = f"""
    WITH obs AS (
        SELECT local_date, CASE WHEN %(var)s = 'TMAX_DAILY' THEN tmax_f ELSE tmin_f END AS obs
          FROM daily_obs
         WHERE station = %(station)s
           AND local_date BETWEEN %(start)s AND %(end)s
    ),
    fc AS (
        SELECT valid_time::date AS valid_date,
               run_time,
               {agg}(value) AS fc_value,
               (valid_time::date - run_time::date) AS lead_day
          FROM det_forecast
         WHERE station = %(station)s AND model = %(model)s AND var = 'TMP_2M'
           AND valid_time::date BETWEEN %(start)s AND %(end)s
         GROUP BY valid_time::date, run_time
    )
    SELECT EXTRACT(MONTH FROM fc.valid_date)::int AS month,
           fc.lead_day::int,
           fc.fc_value,
           obs.obs
      FROM fc JOIN obs ON fc.valid_date = obs.local_date
     WHERE fc.fc_value IS NOT NULL AND obs.obs IS NOT NULL
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, dict(station=station, model=model, var=var, start=start, end=end_date))
        return [(r["month"], r["lead_day"], r["fc_value"], r["obs"]) for r in cur.fetchall()]


def recompute(end_date: date | None = None) -> None:
    """Recompute and upsert bias rows for all active stations."""
    end_date = end_date or datetime.now(tz=timezone.utc).date()
    rows: list[dict] = []

    for code in ACTIVE_STATIONS:
        for model, collector in (
            ("NBM_QMD", _collect_pairs),
            ("HRRR", _collect_det_pairs),
        ):
            for var in ("TMAX_DAILY", "TMIN_DAILY"):
                pairs = collector(code, model, var, end_date)
                if not pairs:
                    continue
                # Bucket by (month, lead_day)
                buckets: dict[tuple[int, int], list[float]] = {}
                for month, lead, fc, obs in pairs:
                    buckets.setdefault((month, lead), []).append(fc - obs)
                for (month, lead), errs in buckets.items():
                    if len(errs) < 3:
                        continue
                    arr = np.asarray(errs, dtype=float)
                    rows.append(
                        dict(
                            station=code,
                            model=model,
                            var=var,
                            month=int(month),
                            lead_day=int(lead),
                            mean_bias_f=float(arr.mean()),
                            stddev_f=float(arr.std(ddof=1)),
                            sample_size=int(arr.size),
                        )
                    )

    if rows:
        persistence.upsert_station_bias(rows)
        log.info("Updated %d bias rows", len(rows))


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
