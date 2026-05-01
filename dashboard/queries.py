"""All SQL the dashboard runs. Single source of truth so the UI doesn't
sprout one-off queries scattered through it."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from weather_bot.data import persistence


def _df(sql: str, params=()) -> pd.DataFrame:
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def latest_health() -> pd.DataFrame:
    return _df("SELECT * FROM health_check_latest ORDER BY station, component")


def health_history(component_like: str = "%", hours: int = 48) -> pd.DataFrame:
    return _df("""
        SELECT * FROM health_check
         WHERE component LIKE %s AND ts > now() - (%s || ' hours')::interval
         ORDER BY ts ASC
    """, (component_like, hours))


def open_positions() -> pd.DataFrame:
    return _df("""
        SELECT pf.id, pf.ts, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees,
               km.station, km.var, km.valid_date, km.lower_f, km.upper_f,
               (km.valid_date - CURRENT_DATE)::int AS days_to_settle,
               ms.yes_ask, ms.yes_bid
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          LEFT JOIN LATERAL (
              SELECT yes_ask, yes_bid FROM market_snapshot
               WHERE ticker = pf.ticker ORDER BY ts DESC LIMIT 1
          ) ms ON true
         WHERE pf.settled = FALSE
         ORDER BY km.valid_date, pf.ts
    """)


def signals_today() -> pd.DataFrame:
    return _df("""
        SELECT s.ts, s.ticker, s.side, s.fair_prob, s.market_ask, s.market_bid,
               s.edge, s.size_usd, s.action, s.notes, km.station, km.var,
               km.valid_date, km.lower_f, km.upper_f
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
         WHERE s.ts >= CURRENT_DATE
         ORDER BY s.ts DESC
    """)


def per_fill_ledger(days_back: int = 14) -> pd.DataFrame:
    return _df("""
        SELECT pf.id, pf.ts AS fill_ts, pf.ticker, pf.side, pf.price, pf.contracts,
               pf.fees, pf.payout, pf.settled, km.station, km.var, km.valid_date,
               km.lower_f, km.upper_f,
               s.fair_prob, s.market_ask, s.market_bid,
               (pf.payout - pf.price) * pf.contracts - pf.fees AS realized_pnl,
               (s.fair_prob*(1 - pf.price) - (1 - s.fair_prob)*pf.price) * pf.contracts - pf.fees AS expected_pnl,
               ABS(s.fair_prob - (s.market_ask + s.market_bid)/2.0) AS divergence
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          LEFT JOIN signal s ON s.id = pf.signal_id
         WHERE km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
         ORDER BY pf.ts DESC
    """, (days_back,))


def daily_calibration(days_back: int = 14) -> pd.DataFrame:
    return _df("""
        SELECT km.valid_date, km.station,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE pf.payout > 0) AS wins,
               SUM((pf.payout - pf.price) * pf.contracts - pf.fees) AS realized,
               SUM((s.fair_prob*(1 - pf.price) - (1 - s.fair_prob)*pf.price) * pf.contracts - pf.fees) AS expected
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          JOIN signal s ON s.id = pf.signal_id
         WHERE pf.settled = TRUE
           AND km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
           AND s.fair_prob IS NOT NULL
         GROUP BY km.valid_date, km.station
         ORDER BY km.valid_date, km.station
    """, (days_back,))


def reliability_bins(days_back: int = 30, station: str | None = None) -> pd.DataFrame:
    """For each forecast-prob decile, count predictions and observed wins.
    Returns the standard reliability diagram raw data."""
    sql = """
    WITH fills AS (
        SELECT (CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END) AS p_side,
               CASE WHEN pf.payout > 0 THEN 1 ELSE 0 END AS won,
               km.station
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          JOIN signal s ON s.id = pf.signal_id
         WHERE pf.settled = TRUE
           AND km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
           AND s.fair_prob IS NOT NULL
           AND (%s::text IS NULL OR km.station = %s)
    )
    SELECT WIDTH_BUCKET(p_side, 0, 1, 10) AS bin,
           AVG(p_side) AS mean_pred,
           AVG(won::float) AS observed_freq,
           COUNT(*) AS n,
           station
      FROM fills
     GROUP BY WIDTH_BUCKET(p_side, 0, 1, 10), station
     HAVING COUNT(*) >= 2
     ORDER BY station, bin
    """
    return _df(sql, (days_back, station, station))


def bias_table_summary() -> pd.DataFrame:
    return _df("""
        SELECT station, model, var, month, lead_day, sample_size,
               ROUND(mean_bias_f::numeric, 2) AS mean_bias_f,
               ROUND(stddev_f::numeric, 2) AS stddev_f, updated_at
          FROM station_bias
         ORDER BY station, model, var, month, lead_day
    """)


def bias_drift_recent(hours: int = 168) -> pd.DataFrame:
    return _df("""
        SELECT * FROM bias_drift_event
         WHERE detected_at > now() - (%s || ' hours')::interval
         ORDER BY detected_at DESC
    """, (hours,))


def nbm_cycles_for(station: str, valid_date: date, var: str = "TMAX_DAILY") -> pd.DataFrame:
    return _df("""
        SELECT run_time, percentile, value
          FROM prob_forecast
         WHERE station = %s AND valid_date = %s AND var = %s
         ORDER BY run_time, percentile
    """, (station, valid_date, var))


def latest_distribution_inputs(station: str, valid_date: date, var: str) -> dict:
    """Pull the percentile rows + HRRR same-day max + intraday floor that
    feed the distribution builder. Used by the live distribution chart."""
    out = {}
    out["nbm"] = _df("""
        SELECT percentile, value FROM prob_forecast
         WHERE station=%s AND valid_date=%s AND var=%s
           AND run_time = (SELECT MAX(run_time) FROM prob_forecast
                            WHERE station=%s AND valid_date=%s AND var=%s)
         ORDER BY percentile
    """, (station, valid_date, var, station, valid_date, var))
    out["hrrr"] = _df("""
        SELECT MAX(value) AS tmax FROM det_forecast
         WHERE station=%s AND model='HRRR' AND var='TMP_2M'
           AND valid_time::date = %s
           AND run_time = (SELECT MAX(run_time) FROM det_forecast
                            WHERE station=%s AND model='HRRR' AND var='TMP_2M'
                              AND valid_time::date=%s)
    """, (station, valid_date, station, valid_date))
    return out


def kalshi_buckets_today(station: str, valid_date: date, var: str) -> pd.DataFrame:
    return _df("""
        SELECT ticker, lower_f, upper_f, status
          FROM kalshi_market
         WHERE station = %s AND valid_date = %s AND var = %s
         ORDER BY COALESCE(lower_f, -999), COALESCE(upper_f, 999)
    """, (station, valid_date, var))


def trade_eligible_stations() -> list[str]:
    from weather_bot.config import ACTIVE_TRADE_STATIONS
    return list(ACTIVE_TRADE_STATIONS)


def fetch_stations() -> list[str]:
    from weather_bot.config import ACTIVE_FETCH_STATIONS
    return list(ACTIVE_FETCH_STATIONS)
