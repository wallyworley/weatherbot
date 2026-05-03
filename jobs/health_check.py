"""Hourly health-check tripwire.

Computes a small set of metrics across DATA / MODEL / MARKETS / RISK / PNL,
classifies each as GREEN / AMBER / RED, and writes one row per (station,
component) into the health_check table.

The trade loop reads health_check_latest to refuse new positions on RED
stations. The dashboard reads it to render status tiles.

Designed to be run via launchd every 30 min (between trade ticks).
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from weather_bot.config import ACTIVE_FETCH_STATIONS, ACTIVE_TRADE_STATIONS, BANKROLL_USD
from weather_bot.data import persistence

log = logging.getLogger(__name__)

# Thresholds — tuned to the post-fix behavior we observed (Brier ~0.14,
# realized-vs-expected diff ~$5/fill). See docs in dashboard/help_text.py for
# reasoning. Override via --thresholds-json on the command line for tuning.
DEFAULT_THRESHOLDS = {
    # DATA: feed staleness in minutes. Cadence-aware: NBM 6h, HRRR 1h, METAR 30m.
    "data_amber_lag_mult": 1.5,   # >1.5x cadence = AMBER
    "data_red_lag_mult":   3.0,   # >3.0x cadence = RED
    # MODEL: 7-day rolling Brier for settled fills.
    "model_amber_brier":   0.18,
    "model_red_brier":     0.25,
    # MODEL: 7-day rolling |expected - realized| edge gap per settled fill.
    "model_amber_edge_diff_per_fill": 4.0,    # $/fill
    "model_red_edge_diff_per_fill":   8.0,    # $/fill — breach => RED, blocks new fills
    # Edge_diff RED requires this many settled fills first. Without it, a
    # newly-graduated station with 5 unlucky fills triggers RED and blocks
    # itself before there's a real signal. Brier is per-prediction so it can
    # still fire RED at the lower (n>=5) threshold.
    "model_red_edge_diff_min_n": 15,
    # MARKETS: number of open Kalshi markets per station.
    "markets_amber_min":   3,
    "markets_red_min":     1,
    # RISK: open notional as fraction of bankroll. With MAX_POSITION_PCT=0.02
    # and ~10-15 markets open per station, normal exposure runs 20-30%; we
    # only want to fire when the bot has stacked unusually many positions.
    "risk_amber_pct":      0.40,
    "risk_red_pct":        0.60,
    # PNL: 7-day net P&L. Negative is OK; very negative is not.
    "pnl_amber_net":       -50.0,
    "pnl_red_net":         -150.0,
}

FEED_CADENCE_MIN = {
    "NBM": 360,    # 6h between cycles (00/06/12/18Z)
    "HRRR": 60,    # hourly
    # METAR observations are produced hourly at major airport stations (with
    # occasional SPECI bulletins on rapid weather change). The launchd
    # fetcher cron also runs hourly. Right after the top of the hour the
    # newest obs is ~0min old; right before it's ~60min old. We don't want
    # AMBER to fire just because we're approaching the next observation.
    "METAR": 60,
    "KALSHI": 15,
}


# ---------------------------------------------------------------------------
# Metric collectors
# ---------------------------------------------------------------------------
def _data_freshness(thresholds: dict) -> list[dict]:
    """One row per feed: status driven by minutes since latest ingestion."""
    rows: list[dict] = []
    queries = {
        "NBM":   "SELECT MAX(ingested_at) AS t FROM prob_forecast",
        "HRRR":  "SELECT MAX(ingested_at) AS t FROM det_forecast WHERE model='HRRR'",
        "METAR": "SELECT MAX(obs_time)    AS t FROM metar_obs",
        "KALSHI":"SELECT MAX(updated_at)  AS t FROM kalshi_market",
    }
    now = datetime.now(tz=timezone.utc)
    with persistence.connect() as conn, conn.cursor() as cur:
        for feed, sql in queries.items():
            cur.execute(sql)
            r = cur.fetchone()
            t = r["t"] if r else None
            if t is None:
                rows.append(_health_row("GLOBAL", f"DATA_{feed}", "RED", None,
                                        detail={"reason": "no rows in table"},
                                        amber_t=None, red_t=None))
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            lag_min = (now - t).total_seconds() / 60.0
            cadence = FEED_CADENCE_MIN[feed]
            amber_t = cadence * thresholds["data_amber_lag_mult"]
            red_t   = cadence * thresholds["data_red_lag_mult"]
            status = "GREEN" if lag_min < amber_t else ("RED" if lag_min > red_t else "AMBER")
            rows.append(_health_row("GLOBAL", f"DATA_{feed}", status, lag_min,
                                    detail={"lag_min": round(lag_min, 1),
                                            "cadence_min": cadence,
                                            "last_ingest": t.isoformat()},
                                    amber_t=amber_t, red_t=red_t))
    return rows


def _model_calibration(thresholds: dict) -> list[dict]:
    """Per trade-station, compute 7d Brier + 7d realized-vs-expected edge gap."""
    rows: list[dict] = []
    sql = """
    WITH settled AS (
        SELECT pf.id, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees, pf.payout,
               km.station, km.valid_date,
               s.fair_prob, s.market_ask, s.market_bid,
               (s.fair_prob*(1.0 - pf.price) - (1.0 - s.fair_prob)*pf.price) * pf.contracts - pf.fees AS expected,
               (pf.payout - pf.price) * pf.contracts - pf.fees AS realized,
               CASE WHEN pf.payout > 0 THEN 1.0 ELSE 0.0 END AS outcome
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          JOIN signal s ON s.id = pf.signal_id
         WHERE pf.settled = TRUE
           AND km.valid_date >= CURRENT_DATE - INTERVAL '7 days'
           AND s.fair_prob IS NOT NULL
    )
    SELECT station,
           COUNT(*) AS n,
           AVG((CASE WHEN side='YES' THEN fair_prob ELSE 1.0 - fair_prob END - outcome) ^ 2) AS brier,
           SUM(expected) AS sum_expected,
           SUM(realized) AS sum_realized
      FROM settled
     GROUP BY station
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        results = {r["station"]: r for r in cur.fetchall()}

    for station in ACTIVE_TRADE_STATIONS:
        r = results.get(station)
        if r is None or r["n"] is None or r["n"] < 5:
            rows.append(_health_row(station, "MODEL", "AMBER", None,
                                    detail={"reason": "insufficient settled fills (need >=5 in 7d)",
                                            "n": int(r["n"]) if r else 0},
                                    amber_t=5, red_t=None))
            continue
        n = int(r["n"])
        brier = float(r["brier"] or 0.0)
        diff = float((r["sum_realized"] or 0) - (r["sum_expected"] or 0))
        diff_per = diff / n
        # Combine Brier + edge_diff: worst of the two drives status.
        b_status = ("GREEN" if brier < thresholds["model_amber_brier"]
                    else "RED"   if brier >= thresholds["model_red_brier"]
                    else "AMBER")
        d_abs = abs(diff_per)
        # edge_diff RED requires a minimum sample size — protects newly-active
        # stations from false RED alerts on small-sample variance.
        red_min_n = thresholds["model_red_edge_diff_min_n"]
        d_red_eligible = n >= red_min_n
        d_status = ("GREEN" if d_abs < thresholds["model_amber_edge_diff_per_fill"]
                    else "RED" if (d_abs >= thresholds["model_red_edge_diff_per_fill"] and d_red_eligible)
                    else "AMBER")
        worst = "RED" if "RED" in (b_status, d_status) else ("AMBER" if "AMBER" in (b_status, d_status) else "GREEN")
        rows.append(_health_row(station, "MODEL", worst, brier,
                                detail={"brier_7d": round(brier, 4),
                                        "edge_diff_per_fill": round(diff_per, 2),
                                        "n_settled_7d": n,
                                        "brier_status": b_status,
                                        "edge_diff_status": d_status},
                                amber_t=thresholds["model_amber_brier"],
                                red_t=thresholds["model_red_brier"]))
    return rows


def _markets_open(thresholds: dict) -> list[dict]:
    rows: list[dict] = []
    sql = """SELECT station, COUNT(*) AS n FROM kalshi_market
              WHERE status IN ('open','active') AND valid_date >= CURRENT_DATE
              GROUP BY station"""
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        results = {r["station"]: int(r["n"]) for r in cur.fetchall()}

    for station in ACTIVE_TRADE_STATIONS:
        n = results.get(station, 0)
        status = ("GREEN" if n >= thresholds["markets_amber_min"]
                  else "RED" if n < thresholds["markets_red_min"]
                  else "AMBER")
        rows.append(_health_row(station, "MARKETS", status, float(n),
                                detail={"n_open": n},
                                amber_t=thresholds["markets_amber_min"],
                                red_t=thresholds["markets_red_min"]))
    return rows


def _risk_exposure(thresholds: dict) -> list[dict]:
    rows: list[dict] = []
    sql = """SELECT km.station,
                    SUM(pf.price * pf.contracts) AS notional,
                    COUNT(*) AS n_open
               FROM paper_fill pf
               JOIN kalshi_market km ON km.ticker = pf.ticker
              WHERE pf.settled = FALSE
              GROUP BY km.station"""
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        results = {r["station"]: r for r in cur.fetchall()}

    for station in ACTIVE_TRADE_STATIONS:
        r = results.get(station)
        if r is None:
            rows.append(_health_row(station, "RISK", "GREEN", 0.0,
                                    detail={"open_notional": 0.0, "n_open": 0,
                                            "bankroll_pct": 0.0},
                                    amber_t=thresholds["risk_amber_pct"],
                                    red_t=thresholds["risk_red_pct"]))
            continue
        notional = float(r["notional"] or 0.0)
        pct = notional / max(BANKROLL_USD, 1.0)
        status = ("GREEN" if pct < thresholds["risk_amber_pct"]
                  else "RED" if pct >= thresholds["risk_red_pct"]
                  else "AMBER")
        rows.append(_health_row(station, "RISK", status, pct,
                                detail={"open_notional": round(notional, 2),
                                        "n_open": int(r["n_open"]),
                                        "bankroll_pct": round(pct, 4)},
                                amber_t=thresholds["risk_amber_pct"],
                                red_t=thresholds["risk_red_pct"]))
    return rows


def _pnl_7d(thresholds: dict) -> list[dict]:
    rows: list[dict] = []
    sql = """SELECT km.station,
                    SUM((pf.payout - pf.price) * pf.contracts - pf.fees) AS net
               FROM paper_fill pf
               JOIN kalshi_market km ON km.ticker = pf.ticker
              WHERE pf.settled = TRUE
                AND km.valid_date >= CURRENT_DATE - INTERVAL '7 days'
              GROUP BY km.station"""
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        results = {r["station"]: float(r["net"] or 0.0) for r in cur.fetchall()}

    for station in ACTIVE_TRADE_STATIONS:
        net = results.get(station, 0.0)
        status = ("GREEN" if net > thresholds["pnl_amber_net"]
                  else "RED" if net < thresholds["pnl_red_net"]
                  else "AMBER")
        rows.append(_health_row(station, "PNL", status, net,
                                detail={"net_7d": round(net, 2)},
                                amber_t=thresholds["pnl_amber_net"],
                                red_t=thresholds["pnl_red_net"]))
    return rows


# ---------------------------------------------------------------------------
# Helpers + persistence
# ---------------------------------------------------------------------------
def _health_row(station, component, status, metric_value,
                detail=None, amber_t=None, red_t=None) -> dict:
    return {
        "station": station,
        "component": component,
        "status": status,
        "metric_value": metric_value,
        "threshold_amber": amber_t,
        "threshold_red": red_t,
        "detail": json.dumps(detail or {}),
    }


def _upsert_health(rows: Iterable[dict]) -> None:
    sql = """
    INSERT INTO health_check
        (station, component, status, metric_value, threshold_amber, threshold_red, detail)
    VALUES (%(station)s, %(component)s, %(status)s, %(metric_value)s,
            %(threshold_amber)s, %(threshold_red)s, %(detail)s::jsonb)
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(sql, r)
        conn.commit()


def run(thresholds: dict | None = None, fire_alerts: bool = True) -> list[dict]:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    rows: list[dict] = []
    rows += _data_freshness(th)
    rows += _model_calibration(th)
    rows += _markets_open(th)
    rows += _risk_exposure(th)
    rows += _pnl_7d(th)
    _upsert_health(rows)
    log.info("health_check: wrote %d rows (%s RED, %s AMBER, %s GREEN)",
             len(rows),
             sum(1 for r in rows if r["status"] == "RED"),
             sum(1 for r in rows if r["status"] == "AMBER"),
             sum(1 for r in rows if r["status"] == "GREEN"))
    if fire_alerts:
        # Imported lazily to keep health_check importable in environments
        # without alert dependencies (no Messages.app, no osascript).
        from weather_bot.jobs import alerts
        try:
            alerts.fire()
        except Exception as exc:
            log.warning("alerts.fire() raised — continuing: %s", exc)
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds-json", help="JSON file overriding default thresholds")
    args = ap.parse_args()
    overrides = None
    if args.thresholds_json:
        with open(args.thresholds_json) as f:
            overrides = json.load(f)
    run(thresholds=overrides)
