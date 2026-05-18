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

from weather_bot.config import ACTIVE_FETCH_STATIONS, ACTIVE_TRADE_STATIONS, BANKROLL_USD, STATIONS
from weather_bot.data import persistence

log = logging.getLogger(__name__)

# Thresholds — tuned to the post-fix behavior we observed (Brier ~0.14,
# realized-vs-expected diff ~$5/fill). See docs in dashboard/help_text.py for
# reasoning. Override via --thresholds-json on the command line for tuning.
DEFAULT_THRESHOLDS = {
    # DATA: feed staleness in minutes. Cadence-aware: NBM/GFS/ECMWF 6h; HRRR 1h;
    # HFMETAR is polled every 5min but tolerated at 10min because IEM/MADIS
    # publication lag commonly runs 10-15min; Kalshi snapshots are 5min.
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
    # SNAPSHOT QUALITY: % of latest market_snapshot rows with both YES bid+ask
    # populated, and median age in minutes since the last snapshot per market.
    # Backtest fidelity collapses if snapshots are stale or missing — this is
    # the canary for "are we capturing the orderbook reliably?".
    "snap_amber_yes_fill_pct":    90.0,    # below 90% YES fill = AMBER
    "snap_red_yes_fill_pct":      70.0,    # below 70% = RED
    "snap_amber_age_min":         20.0,    # median age > 20 min = AMBER
    "snap_red_age_min":           45.0,    # > 45 min = RED
}

FEED_CADENCE_MIN = {
    "NBM": 360,    # 6h between cycles (00/06/12/18Z)
    "HRRR": 60,    # hourly
    "GFS": 360,    # Open-Meteo pull runs hourly, but new model cycles are 6-hourly
    "ECMWF": 360,  # Open-Meteo pull runs hourly, but new model cycles are 6-hourly
    # METAR observations are produced hourly at major airport stations (with
    # occasional SPECI bulletins on rapid weather change). The launchd
    # fetcher cron also runs hourly. Right after the top of the hour the
    # newest obs is ~0min old; right before it's ~60min old. We don't want
    # AMBER to fire just because we're approaching the next observation.
    "METAR": 60,
    # ASOS stations produce 5-min HFMETAR and the VPS timer polls every 5min,
    # but IEM Iowa Mesonet's actual publication lag for KMDW/KMIA runs 25-35min
    # most days. Setting cadence=15 (red threshold 45min) reflects that reality
    # so transient IEM delays don't trip RED. If the bot's polling timer ever
    # actually fails, lag would climb well past 45min and still trip.
    "HFMETAR": 15,
    "KALSHI": 5,
}


# ---------------------------------------------------------------------------
# Metric collectors
# ---------------------------------------------------------------------------
def _data_freshness(thresholds: dict) -> list[dict]:
    """One row per feed: status driven by minutes since latest ingestion.

    METAR is split per active station because different stations now use
    different sources (ASOS=HFMETAR ~5 min cadence, KNYC=hourly METAR), and
    a fresh row at one station would otherwise mask a stalled feed at another.
    """
    rows: list[dict] = []
    global_queries = {
        "NBM":   "SELECT MAX(ingested_at) AS t FROM prob_forecast",
        "HRRR":  "SELECT MAX(ingested_at) AS t FROM det_forecast WHERE model='HRRR'",
        "GFS":   "SELECT MAX(ingested_at) AS t FROM det_forecast WHERE model='GFS'",
        "ECMWF": "SELECT MAX(ingested_at) AS t FROM det_forecast WHERE model='ECMWF'",
        "KALSHI":"SELECT MAX(updated_at)  AS t FROM kalshi_market",
    }
    now = datetime.now(tz=timezone.utc)
    with persistence.connect() as conn, conn.cursor() as cur:
        for feed, sql in global_queries.items():
            cur.execute(sql)
            r = cur.fetchone()
            rows.append(_freshness_row("GLOBAL", feed, r["t"] if r else None,
                                        FEED_CADENCE_MIN[feed], thresholds, now))

        # Per-station METAR freshness — the feed kind depends on is_asos so
        # the cadence used in the threshold also depends on the station.
        for code in ACTIVE_FETCH_STATIONS:
            station = STATIONS.get(code)
            if station is None:
                continue
            feed = "HFMETAR" if station.is_asos else "METAR"
            cur.execute("SELECT MAX(obs_time) AS t FROM metar_obs WHERE station=%s", (code,))
            r = cur.fetchone()
            rows.append(_freshness_row(code, feed, r["t"] if r else None,
                                        FEED_CADENCE_MIN[feed], thresholds, now))
    return rows


def _freshness_row(station: str, feed: str, t, cadence: int, thresholds: dict, now) -> dict:
    if t is None:
        return _health_row(station, f"DATA_{feed}", "RED", None,
                           detail={"reason": "no rows in table"},
                           amber_t=None, red_t=None)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    lag_min = (now - t).total_seconds() / 60.0
    amber_t = cadence * thresholds["data_amber_lag_mult"]
    red_t   = cadence * thresholds["data_red_lag_mult"]
    status = "GREEN" if lag_min < amber_t else ("RED" if lag_min > red_t else "AMBER")
    return _health_row(station, f"DATA_{feed}", status, lag_min,
                       detail={"lag_min": round(lag_min, 1),
                               "cadence_min": cadence,
                               "last_ingest": t.isoformat()},
                       amber_t=amber_t, red_t=red_t)


def _model_calibration(thresholds: dict) -> list[dict]:
    """Per trade-station, compute 7d Brier + 7d realized-vs-expected edge gap."""
    rows: list[dict] = []
    sql = """
    WITH settled AS (
        SELECT pf.id, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees, pf.payout,
               km.station, km.valid_date,
               s.fair_prob, s.market_ask, s.market_bid,
               ((CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END) * (1.0 - pf.price)
                - (1.0 - (CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END)) * pf.price)
                * pf.contracts - pf.fees AS expected,
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


def _snapshot_quality(thresholds: dict) -> list[dict]:
    """One row per active fetch station: % of open markets with fresh, complete
    market_snapshot rows. Detects gaps in the orderbook capture pipeline that
    would silently corrupt backtests later.

    Metrics per station:
      - n_open_markets:       count of currently-open kalshi_market rows
      - latest_snap_count:    open markets with at least one snapshot in 24h
      - yes_fill_pct:         of latest snapshots, % with both yes_ask + yes_bid
      - no_fill_pct:          same for NO side (informational; not status-driving)
      - median_age_min:       median minutes since latest snapshot per market
      - max_age_min:          oldest market's most-recent snapshot age
      - median_spread:        median (yes_ask − yes_bid) spread

    Status driven by yes_fill_pct AND median_age_min (whichever is worse).
    """
    sql = """
    WITH latest_snaps AS (
        SELECT DISTINCT ON (km.ticker)
               km.station, km.ticker, ms.ts,
               ms.yes_ask, ms.yes_bid, ms.no_ask, ms.no_bid
          FROM kalshi_market km
          LEFT JOIN market_snapshot ms ON ms.ticker = km.ticker
         WHERE km.status IN ('open','active') AND km.valid_date >= CURRENT_DATE
         ORDER BY km.ticker, ms.ts DESC NULLS LAST
    )
    SELECT station,
           COUNT(*) AS n_open_markets,
           COUNT(ts) AS latest_snap_count,
           SUM(CASE WHEN yes_ask IS NOT NULL AND yes_bid IS NOT NULL THEN 1 ELSE 0 END) AS n_yes_filled,
           SUM(CASE WHEN no_ask  IS NOT NULL AND no_bid  IS NOT NULL THEN 1 ELSE 0 END) AS n_no_filled,
           -- PERCENTILE_CONT can't operate on TIMESTAMPTZ directly; convert
           -- the (now - ts) interval to epoch seconds first, then divide to min.
           PERCENTILE_CONT(0.5) WITHIN GROUP
               (ORDER BY EXTRACT(EPOCH FROM (now() - ts))) / 60.0 AS median_age_min,
           EXTRACT(EPOCH FROM (now() - MIN(ts))) / 60.0 AS max_age_min,
           PERCENTILE_CONT(0.5) WITHIN GROUP
               (ORDER BY (yes_ask - yes_bid)) AS median_spread
      FROM latest_snaps
     WHERE ts IS NOT NULL
     GROUP BY station
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        results = {r["station"]: r for r in cur.fetchall()}

    rows: list[dict] = []
    for station in ACTIVE_TRADE_STATIONS:
        r = results.get(station)
        if r is None or not r["n_open_markets"]:
            rows.append(_health_row(station, "DATA_SNAPSHOT", "AMBER", None,
                                    detail={"reason": "no open markets to snapshot"},
                                    amber_t=None, red_t=None))
            continue
        n_open = int(r["n_open_markets"])
        snap_count = int(r["latest_snap_count"] or 0)
        yes_fill_pct = (100.0 * int(r["n_yes_filled"] or 0) / n_open) if n_open else 0.0
        no_fill_pct  = (100.0 * int(r["n_no_filled"]  or 0) / n_open) if n_open else 0.0
        median_age = float(r["median_age_min"]) if r["median_age_min"] is not None else float("inf")
        max_age = float(r["max_age_min"]) if r["max_age_min"] is not None else float("inf")
        median_spread = float(r["median_spread"]) if r["median_spread"] is not None else None

        # Fill status (worst of YES-fill % and median snapshot age)
        fill_status = ("GREEN" if yes_fill_pct >= thresholds["snap_amber_yes_fill_pct"]
                       else "RED" if yes_fill_pct < thresholds["snap_red_yes_fill_pct"]
                       else "AMBER")
        age_status = ("GREEN" if median_age < thresholds["snap_amber_age_min"]
                      else "RED" if median_age > thresholds["snap_red_age_min"]
                      else "AMBER")
        worst = "RED" if "RED" in (fill_status, age_status) else ("AMBER" if "AMBER" in (fill_status, age_status) else "GREEN")
        rows.append(_health_row(station, "DATA_SNAPSHOT", worst, yes_fill_pct,
                                detail={"n_open": n_open, "n_with_snap": snap_count,
                                        "yes_fill_pct": round(yes_fill_pct, 1),
                                        "no_fill_pct":  round(no_fill_pct, 1),
                                        "median_age_min": round(median_age, 1),
                                        "max_age_min":   round(max_age, 1),
                                        "median_spread": round(median_spread, 3) if median_spread is not None else None,
                                        "fill_status": fill_status,
                                        "age_status": age_status},
                                amber_t=thresholds["snap_amber_yes_fill_pct"],
                                red_t=thresholds["snap_red_yes_fill_pct"]))
    return rows


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
    rows += _snapshot_quality(th)
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
