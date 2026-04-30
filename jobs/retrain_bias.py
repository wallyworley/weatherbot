"""Retrain station_bias from historical prob_forecast + daily_obs.

Convention: lead_day = (valid_date - run_time_local_date) — the calendar gap
between when the cycle was issued (in station-local time) and the target date.
This is the only convention that maps cleanly to the current ingestion model,
which retains exactly one cycle row per (station, model, var, valid_date,
percentile). Under this convention, each cycle naturally has a single lead.

Sign convention: mean_bias_f = fcst - obs. Positive = forecast too warm.
Matches distribution.py's `cdf.shift -= effective_bias` semantic.
"""
from __future__ import annotations

import argparse
import logging
from typing import Sequence

from psycopg.rows import dict_row

from weather_bot.data.persistence import connect

log = logging.getLogger(__name__)

_BIAS_PERCENTILE = 50
_DEFAULT_TZ = "America/New_York"

_RETRAIN_SQL = """
WITH paired AS (
    SELECT
        pf.station, pf.model, pf.var,
        pf.run_time, pf.valid_date, pf.value AS fcst_f,
        EXTRACT(MONTH FROM pf.valid_date)::int AS month,
        GREATEST(
            0,
            (pf.valid_date - (pf.run_time AT TIME ZONE %(tz)s)::date)::int
        ) AS lead_day,
        CASE pf.var
            WHEN 'TMAX_DAILY' THEN obs.tmax_f
            WHEN 'TMIN_DAILY' THEN obs.tmin_f
        END AS obs_f
    FROM prob_forecast pf
    JOIN daily_obs obs
      ON obs.station    = pf.station
     AND obs.local_date = pf.valid_date
    WHERE pf.percentile = %(pct)s
      AND pf.var IN ('TMAX_DAILY', 'TMIN_DAILY')
      AND (%(station)s::text IS NULL OR pf.station = %(station)s)
)
SELECT
    station, model, var, month, lead_day,
    AVG(fcst_f - obs_f)::double precision                       AS mean_bias_f,
    COALESCE(STDDEV_SAMP(fcst_f - obs_f), 0)::double precision  AS stddev_f,
    COUNT(*)::int                                               AS sample_size
FROM paired
WHERE lead_day BETWEEN 0 AND %(max_lead)s
  AND obs_f  IS NOT NULL
  AND fcst_f IS NOT NULL
GROUP BY station, model, var, month, lead_day
HAVING COUNT(*) >= %(min_n)s
ORDER BY station, model, var, month, lead_day
"""

_UPSERT_SQL = """
INSERT INTO station_bias
    (station, model, var, month, lead_day, mean_bias_f, stddev_f, sample_size, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (station, model, var, month, lead_day) DO UPDATE SET
    mean_bias_f = EXCLUDED.mean_bias_f,
    stddev_f    = EXCLUDED.stddev_f,
    sample_size = EXCLUDED.sample_size,
    updated_at  = now()
"""


def retrain(station=None, min_n=5, max_lead=7, tz=_DEFAULT_TZ, dry_run=False):
    params = {"pct": _BIAS_PERCENTILE, "station": station,
              "min_n": min_n, "max_lead": max_lead, "tz": tz}
    with connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_RETRAIN_SQL, params)
            rows = cur.fetchall()
        log.info("Computed %d bias rows (station=%s min_n=%d max_lead=%d tz=%s)",
                 len(rows), station or "*", min_n, max_lead, tz)
        for r in rows:
            log.info("  %s/%s/%s m=%02d l=%d n=%d mean=%+.2f std=%.2f",
                     r["station"], r["model"], r["var"], r["month"], r["lead_day"],
                     r["sample_size"], float(r["mean_bias_f"]), float(r["stddev_f"]))
        if dry_run:
            log.info("dry-run: no writes")
            return len(rows)
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(_UPSERT_SQL,
                    (r["station"], r["model"], r["var"], r["month"], r["lead_day"],
                     r["mean_bias_f"], r["stddev_f"], r["sample_size"]))
        conn.commit()
    log.info("Upserted %d rows into station_bias", len(rows))
    return len(rows)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--station", default=None)
    p.add_argument("--min-n", type=int, default=5)
    p.add_argument("--max-lead", type=int, default=7)
    p.add_argument("--tz", default=_DEFAULT_TZ)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    retrain(station=args.station, min_n=args.min_n, max_lead=args.max_lead,
            tz=args.tz, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
