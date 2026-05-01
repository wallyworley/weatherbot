"""Nightly bias drift detector.

After bias retrain runs, snapshot station_bias into station_bias_history and
diff against the previous snapshot. Flag rows that moved >2σ relative to the
previous stddev — those are the canary for "did our fetcher break again?"

The 04-30 calibration collapse would have been caught by this: the corrupted
NBM data made the recomputed bias jump several degrees overnight, which is
many σ away from the previous day's value.

Run after jobs.retrain_bias in the nightly schedule.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from weather_bot.data import persistence

log = logging.getLogger(__name__)

DEFAULT_WATCH_SIGMA = 1.5
DEFAULT_ALERT_SIGMA = 3.0


def _snapshot_today(snapshot_date: date) -> int:
    """Copy current station_bias into station_bias_history@snapshot_date.

    Idempotent: if the snapshot already exists for this date, we replace it.
    """
    sql = """
    INSERT INTO station_bias_history
        (snapshot_date, station, model, var, month, lead_day,
         mean_bias_f, stddev_f, sample_size)
    SELECT %s, station, model, var, month, lead_day,
           mean_bias_f, stddev_f, sample_size
      FROM station_bias
    ON CONFLICT (snapshot_date, station, model, var, month, lead_day)
    DO UPDATE SET mean_bias_f = EXCLUDED.mean_bias_f,
                  stddev_f    = EXCLUDED.stddev_f,
                  sample_size = EXCLUDED.sample_size
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (snapshot_date,))
        n = cur.rowcount
        conn.commit()
    return n


def _detect_drift(snapshot_date: date, lookback_days: int = 1,
                  watch_sigma: float = DEFAULT_WATCH_SIGMA,
                  alert_sigma: float = DEFAULT_ALERT_SIGMA) -> list[dict]:
    """Compare snapshot_date's bias to (snapshot_date - lookback_days)'s.

    For each (station, model, var, month, lead_day), compute the delta in
    mean_bias_f, normalize by the PREVIOUS stddev, and flag rows where the
    movement exceeds watch_sigma. ALERT severity at alert_sigma.

    Returns the drift rows; caller upserts into bias_drift_event.
    """
    prev_date = snapshot_date - timedelta(days=lookback_days)
    sql = """
    WITH prev AS (
      SELECT * FROM station_bias_history WHERE snapshot_date = %s
    ),
    cur AS (
      SELECT * FROM station_bias_history WHERE snapshot_date = %s
    )
    SELECT cur.station, cur.model, cur.var, cur.month, cur.lead_day,
           prev.mean_bias_f AS prev_mean,
           cur.mean_bias_f  AS new_mean,
           prev.stddev_f    AS prev_std,
           cur.sample_size  AS sample_size,
           ABS(cur.mean_bias_f - prev.mean_bias_f) /
                NULLIF(prev.stddev_f, 0) AS delta_sigma
      FROM cur JOIN prev USING (station, model, var, month, lead_day)
     WHERE prev.stddev_f > 0
       AND ABS(cur.mean_bias_f - prev.mean_bias_f) / NULLIF(prev.stddev_f, 0) >= %s
     ORDER BY delta_sigma DESC
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (prev_date, snapshot_date, watch_sigma))
        rows = []
        for r in cur.fetchall():
            severity = "ALERT" if r["delta_sigma"] >= alert_sigma else "WATCH"
            rows.append({**r, "severity": severity})
    return rows


def _record_events(events: list[dict]) -> int:
    if not events:
        return 0
    sql = """
    INSERT INTO bias_drift_event
        (station, model, var, month, lead_day,
         prev_mean, new_mean, delta_sigma, sample_size, severity)
    VALUES (%(station)s, %(model)s, %(var)s, %(month)s, %(lead_day)s,
            %(prev_mean)s, %(new_mean)s, %(delta_sigma)s, %(sample_size)s, %(severity)s)
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        for e in events:
            cur.execute(sql, e)
        conn.commit()
    return len(events)


def run(snapshot_date: date | None = None, watch_sigma: float = DEFAULT_WATCH_SIGMA,
        alert_sigma: float = DEFAULT_ALERT_SIGMA) -> dict:
    snapshot_date = snapshot_date or date.today()
    snapshot_n = _snapshot_today(snapshot_date)
    log.info("Snapshotted station_bias for %s (%d rows touched)", snapshot_date, snapshot_n)

    events = _detect_drift(snapshot_date, watch_sigma=watch_sigma, alert_sigma=alert_sigma)
    n_alerts = sum(1 for e in events if e["severity"] == "ALERT")
    n_watch  = sum(1 for e in events if e["severity"] == "WATCH")
    if events:
        _record_events(events)
        for e in events[:10]:
            log.warning("DRIFT %s %s/%s/%s/m%d/l%d: %.2f→%.2f Δ=%.1fσ (%s)",
                        e["severity"], e["station"], e["model"], e["var"],
                        e["month"], e["lead_day"], e["prev_mean"], e["new_mean"],
                        e["delta_sigma"], e["severity"])
    log.info("Drift detection: %d ALERT, %d WATCH (snapshot=%s)", n_alerts, n_watch, snapshot_date)
    return {"snapshot": snapshot_n, "alerts": n_alerts, "watch": n_watch}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch-sigma", type=float, default=DEFAULT_WATCH_SIGMA)
    ap.add_argument("--alert-sigma", type=float, default=DEFAULT_ALERT_SIGMA)
    args = ap.parse_args()
    run(watch_sigma=args.watch_sigma, alert_sigma=args.alert_sigma)
