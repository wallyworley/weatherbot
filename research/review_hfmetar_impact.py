"""One-week post-rollout review of the HFMETAR phase 1+2+3 change.

Phases 1+2+3 (backfill source, bias retrain, AND live live-path switch) all
shipped 2026-05-03. Run this on/after 2026-05-10 to decide whether to do
phase 4 (loosen settle_paper_fills CLI requirement now that daily_obs is
within ~0.05°F of CLI for ASOS stations).

Compares for ASOS stations (KMIA, KMDW):
  pre  = signals/forecasts/fills with valid_date < 2026-05-03 (old bias regime)
  post = signals/forecasts/fills with valid_date >= 2026-05-03

Output sections:
  1. Forecast accuracy: |fcst − CLI| at lead 0/1, before vs after
  2. Paper PnL per settled fill, before vs after
  3. Daily-obs source mix (sanity: should be HFMETAR for KMIA/KMDW post-cutover)

Decision rule (suggested):
  - go phase 3 if KMIA post-cutover MAE is no worse than pre AND PnL/fill is no worse
  - hold if either degraded; investigate before switching live path
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from weather_bot.data import persistence

log = logging.getLogger(__name__)

CUTOVER = date(2026, 5, 3)


_FORECAST_VS_CLI_SQL = """
-- Pick one canonical cycle per (station, valid_date, lead_day): the LATEST
-- run_time available at the (target_date - lead_day) cutoff. Without this
-- dedup, a date with many cycles would be over-counted and skew pre vs post
-- if cycle density differs across the cutover.
-- Lead day uses the station's local timezone (KMDW=Chicago, KMIA=Eastern).
WITH median AS (
    SELECT pf.station, pf.valid_date, pf.run_time, pf.value AS fcst_f,
           GREATEST(0,
             (pf.valid_date - (pf.run_time AT TIME ZONE st.tz)::date)::int
           ) AS lead_day
      FROM prob_forecast pf
      JOIN stations st ON st.code = pf.station
     WHERE pf.percentile = 50
       AND pf.var = 'TMAX_DAILY'
       AND pf.station = ANY(%(stations)s)
), latest_cycle AS (
    SELECT station, valid_date, lead_day,
           MAX(run_time) AS run_time
      FROM median
     WHERE lead_day BETWEEN 0 AND 2
     GROUP BY station, valid_date, lead_day
)
SELECT
    lc.station,
    CASE WHEN lc.valid_date < %(cutover)s THEN 'pre' ELSE 'post' END AS era,
    lc.lead_day,
    COUNT(*) AS n,
    AVG(ABS(m.fcst_f - c.tmax_f))::numeric(5,2) AS mean_abs_err,
    AVG(m.fcst_f - c.tmax_f)::numeric(5,2)      AS signed_bias
  FROM latest_cycle lc
  JOIN median m
    ON m.station = lc.station AND m.valid_date = lc.valid_date
   AND m.lead_day = lc.lead_day AND m.run_time = lc.run_time
  JOIN cli_obs c
    ON c.station = lc.station AND c.local_date = lc.valid_date
 GROUP BY lc.station, era, lc.lead_day
 ORDER BY lc.station, lc.lead_day, era
"""


_PNL_SQL = """
-- payout is stored per-contract (0.0 or 1.0); fees is total per fill.
-- Matches settle_paper_fills:  gross_pnl = (payout - price) * contracts.
SELECT
    m.station,
    CASE WHEN m.valid_date < %(cutover)s THEN 'pre' ELSE 'post' END AS era,
    COUNT(*) AS n_fills,
    SUM((COALESCE(pf.payout, 0) - pf.price) * pf.contracts - pf.fees)::numeric(8,2) AS net_pnl,
    AVG(((COALESCE(pf.payout, 0) - pf.price) * pf.contracts - pf.fees) / NULLIF(pf.contracts, 0))::numeric(6,3) AS pnl_per_contract
  FROM paper_fill pf
  JOIN kalshi_market m ON m.ticker = pf.ticker
 WHERE pf.settled = TRUE
   AND m.station = ANY(%(stations)s)
 GROUP BY m.station, era
 ORDER BY m.station, era
"""


_SOURCE_MIX_SQL = """
SELECT station, source, COUNT(*) AS n,
       MIN(local_date) AS oldest, MAX(local_date) AS newest
  FROM daily_obs
 WHERE station = ANY(%(stations)s)
 GROUP BY station, source
 ORDER BY station, source
"""


def run(stations=("KMIA", "KMDW")):
    params = {"stations": list(stations), "cutover": CUTOVER}
    with persistence.connect() as conn, conn.cursor() as cur:
        print(f"\n=== Forecast vs CLI (TMAX), pre vs post {CUTOVER} ===")
        cur.execute(_FORECAST_VS_CLI_SQL, params)
        for r in cur.fetchall():
            print(dict(r))

        print(f"\n=== Settled paper-fill PnL, pre vs post {CUTOVER} ===")
        cur.execute(_PNL_SQL, params)
        rows = cur.fetchall()
        if not rows:
            print("(no settled fills yet)")
        for r in rows:
            print(dict(r))

        print(f"\n=== daily_obs source mix (sanity) ===")
        cur.execute(_SOURCE_MIX_SQL, params)
        for r in cur.fetchall():
            print(dict(r))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", nargs="+", default=["KMIA", "KMDW"])
    args = ap.parse_args()
    run(stations=args.stations)
