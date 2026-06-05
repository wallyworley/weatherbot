"""All SQL the dashboard runs. Single source of truth so the UI doesn't
sprout one-off queries scattered through it."""
from __future__ import annotations

from datetime import date, timedelta
import time

import pandas as pd
import psycopg

from weather_bot.data import persistence

_CACHE_TTL_SECONDS = 12
_DF_CACHE: dict[tuple[str, object], tuple[float, pd.DataFrame]] = {}

REALIZED_PNL_SQL = """
CASE
    WHEN pf.exit_price IS NOT NULL
        THEN (pf.exit_price - pf.price) * pf.contracts - pf.fees - COALESCE(pf.exit_fees, 0)
    WHEN pf.payout IS NOT NULL
        THEN (pf.payout - pf.price) * pf.contracts - pf.fees
    ELSE NULL
END
"""

# Every "today" reference in the dashboard is from a user reading in ET.
# VPS clock is UTC, so plain CURRENT_DATE rolls over at 8pm ET — when the
# dashboard would suddenly switch to tomorrow's view. All "today" SQL below
# uses `(now() AT TIME ZONE 'America/New_York')::date` inline so each query
# is self-contained when read in isolation. For ts columns (timestamptz),
# convert to the station-local or ET date with `(ts AT TIME ZONE 'America/New_York')::date`
# before comparing.


def _freeze(value):
    """Make DB params hashable for the dashboard's short-lived query cache."""
    if isinstance(value, list):
        return ("__list__", tuple(_freeze(v) for v in value))
    if isinstance(value, tuple):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return ("__dict__", tuple(sorted((k, _freeze(v)) for k, v in value.items())))
    return value


def _run_df(sql: str, params=()) -> pd.DataFrame:
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _df(sql: str, params=()) -> pd.DataFrame:
    key = (sql, _freeze(tuple(params)))
    now = time.monotonic()
    cached = _DF_CACHE.get(key)
    if cached is not None:
        ts, df = cached
        if now - ts <= _CACHE_TTL_SECONDS:
            return df.copy()
    df = _run_df(sql, params)
    _DF_CACHE[key] = (now, df.copy())
    return df


def clear_cache() -> None:
    _DF_CACHE.clear()


def latest_health() -> pd.DataFrame:
    """Latest health-check row per (station, component), filtered to GLOBAL +
    currently-active fetch stations. Excludes stale rows for stations that
    were previously active but have since been removed from
    ACTIVE_FETCH_STATIONS (e.g., KORD after the 2026-05-02 KMDW switch)."""
    from weather_bot.config import ACTIVE_FETCH_STATIONS
    return _df("""
        SELECT * FROM health_check_latest
         WHERE (station = 'GLOBAL' OR station = ANY(%s))
           AND NOT (station = 'GLOBAL' AND component IN ('DATA_METAR', 'DATA_HFMETAR'))
         ORDER BY station, component
    """, (ACTIVE_FETCH_STATIONS,))


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
               (km.valid_date - (now() AT TIME ZONE 'America/New_York')::date)::int AS days_to_settle,
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


def pending_paper_orders() -> pd.DataFrame:
    try:
        return _df("""
            SELECT po.id, po.created_at, po.expires_at, po.ticker, po.side,
                   po.limit_price, po.contracts, po.fees_est, po.notes,
                   km.station, km.var, km.valid_date, km.lower_f, km.upper_f,
                   EXTRACT(EPOCH FROM (po.expires_at - now())) / 60.0 AS ttl_min
              FROM paper_order po
              JOIN kalshi_market km ON km.ticker = po.ticker
             WHERE po.status = 'PENDING'
             ORDER BY po.expires_at ASC, po.created_at ASC
        """)
    except psycopg.errors.UndefinedTable:
        return pd.DataFrame()


def paper_order_counts(days_back: int = 7) -> pd.DataFrame:
    try:
        return _df("""
            SELECT status, COUNT(*) AS n
              FROM paper_order
             WHERE created_at >= now() - (%s || ' days')::interval
             GROUP BY status
             ORDER BY status
        """, (days_back,))
    except psycopg.errors.UndefinedTable:
        return pd.DataFrame()


def signals_today() -> pd.DataFrame:
    return _df("""
        SELECT s.ts, s.ticker, s.side, s.fair_prob, s.market_ask, s.market_bid,
               s.edge, s.size_usd, s.action, s.skip_reason, s.model_votes,
               s.reversal_risk, s.notes,
               km.station, km.var, km.valid_date, km.lower_f, km.upper_f
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
         WHERE (s.ts AT TIME ZONE 'America/New_York')::date >= (now() AT TIME ZONE 'America/New_York')::date
         ORDER BY s.ts DESC
    """)


def skip_breakdown(days_back: int = 7) -> pd.DataFrame:
    """Count SKIP signals by reason over the last N days. Powers the
    'why didn't we trade' forensics view in the Trading tab."""
    return _df("""
        SELECT COALESCE(skip_reason, 'UNCLASSIFIED') AS skip_reason,
               COUNT(*) AS n,
               COUNT(DISTINCT ticker) AS n_tickers
          FROM signal
         WHERE action = 'SKIP'
           AND (ts AT TIME ZONE 'America/New_York')::date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
         GROUP BY skip_reason
         ORDER BY n DESC
    """, (days_back,))


def signal_activity(days_back: int = 7) -> pd.DataFrame:
    """Daily signal counts by action and skip reason."""
    return _df("""
        SELECT ts::date AS day,
               action,
               COALESCE(skip_reason, 'OPEN') AS reason,
               COUNT(*) AS n
          FROM signal
         WHERE (ts AT TIME ZONE 'America/New_York')::date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
         GROUP BY day, action, reason
         ORDER BY day DESC, action, reason
    """, (days_back,))


def profitability_slices(days_back: int = 30) -> pd.DataFrame:
    """Settled-fill P&L by station, lead day, side, and price band.

    Recomputes Kalshi fees at order level so older paper-fill rows remain
    comparable after the fee-methodology correction.
    """
    return _df("""
    WITH fills AS (
        SELECT pf.id, pf.ts, pf.ticker, pf.side, pf.price, pf.contracts, pf.payout,
               pf.exit_price, pf.exit_fees,
               CEIL((0.07 * pf.contracts * pf.price * (1.0 - pf.price)) * 100) / 100.0 AS fees,
               km.station,
               km.valid_date,
               GREATEST(0, (km.valid_date - (pf.ts AT TIME ZONE st.tz)::date)) AS lead_day,
               CASE
                   WHEN pf.price < 0.25 THEN '<25c'
                   WHEN pf.price < 0.50 THEN '25-50c'
                   WHEN pf.price < 0.75 THEN '50-75c'
                   ELSE '75c+'
               END AS price_band,
               CASE WHEN {realized_pnl} > 0 THEN 1 ELSE 0 END AS won,
               {realized_pnl} AS net_pnl,
               ((CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END) - pf.price)
                   * pf.contracts
                   - CEIL((0.07 * pf.contracts * pf.price * (1.0 - pf.price)) * 100) / 100.0 AS model_claimed_ev
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          JOIN stations st ON st.code = km.station
          LEFT JOIN signal s ON s.id = pf.signal_id
         WHERE pf.settled = TRUE
           AND km.valid_date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
    )
    SELECT station,
           lead_day,
           side,
           price_band,
           COUNT(*) AS fills,
           SUM(contracts) AS contracts,
           AVG(price) AS avg_price,
           AVG(won::float) AS win_rate,
           SUM(net_pnl) AS net_pnl,
           AVG(net_pnl) AS pnl_per_fill,
           SUM(model_claimed_ev) AS model_claimed_ev,
           SUM(net_pnl) - SUM(model_claimed_ev) AS realized_vs_claimed
      FROM fills
     GROUP BY station, lead_day, side, price_band
     ORDER BY net_pnl ASC
    """.format(realized_pnl=REALIZED_PNL_SQL), (days_back,))


def per_fill_ledger(days_back: int = 14) -> pd.DataFrame:
    return _df("""
        SELECT pf.id, pf.ts AS fill_ts, pf.ticker, pf.side, pf.price, pf.contracts,
               pf.fees, pf.payout, pf.exit_price, pf.exit_fees, pf.exit_ts,
               pf.exit_reason, pf.settled, km.station, km.var, km.valid_date,
               km.lower_f, km.upper_f,
               s.fair_prob, s.market_ask, s.market_bid,
               {realized_pnl} AS realized_pnl,
               ((CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END) * (1 - pf.price)
                - (1 - (CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END)) * pf.price)
                * pf.contracts - pf.fees AS expected_pnl,
               ABS(s.fair_prob - (s.market_ask + s.market_bid)/2.0) AS divergence
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          LEFT JOIN signal s ON s.id = pf.signal_id
         WHERE km.valid_date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
         ORDER BY pf.ts DESC
    """.format(realized_pnl=REALIZED_PNL_SQL), (days_back,))


def daily_calibration(days_back: int = 14) -> pd.DataFrame:
    return _df("""
        SELECT km.valid_date, km.station,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE pf.payout > 0) AS wins,
               SUM((pf.payout - pf.price) * pf.contracts - pf.fees) AS realized,
               SUM(((CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END) * (1 - pf.price)
                    - (1 - (CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END)) * pf.price)
                    * pf.contracts - pf.fees) AS expected
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          JOIN signal s ON s.id = pf.signal_id
         WHERE pf.settled = TRUE
           AND pf.exit_price IS NULL
           AND pf.payout IS NOT NULL
           AND km.valid_date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
           AND s.fair_prob IS NOT NULL
         GROUP BY km.valid_date, km.station
         ORDER BY km.valid_date, km.station
    """, (days_back,))


def bucket_calibration(days_back: int = 30, n_bins: int = 10) -> pd.DataFrame:
    """Per-probability-bucket calibration: predicted vs observed win rate, with
    Wilson 95% CI on observed_freq. A bucket where mean_pred lands OUTSIDE the
    CI is miscalibrated — model is systematically over- or under-confident at
    that probability range.

    Bin width = 1/n_bins (default 10% deciles). Aggregated across stations
    because per-station per-bin n is too thin for stable estimates.
    """
    sql = """
    WITH fills AS (
        SELECT (CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END) AS p_side,
               CASE WHEN pf.payout > 0 THEN 1 ELSE 0 END AS won
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          JOIN signal s ON s.id = pf.signal_id
         WHERE pf.settled = TRUE
           AND pf.exit_price IS NULL
           AND pf.payout IS NOT NULL
           AND km.valid_date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
           AND s.fair_prob IS NOT NULL
    )
    SELECT bin,
           AVG(p_side) AS mean_pred,
           SUM(won)::float / COUNT(*) AS observed_freq,
           COUNT(*) AS n,
           SUM(won) AS n_won,
           AVG((p_side - won) ^ 2) AS brier_bin
      FROM (SELECT p_side, won, WIDTH_BUCKET(p_side, 0, 1, %s) AS bin FROM fills) f
     GROUP BY bin
     ORDER BY bin
    """
    return _df(sql, (days_back, n_bins))


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
           AND pf.exit_price IS NULL
           AND pf.payout IS NOT NULL
           AND km.valid_date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
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


def event_reliability_bins(days_back: int = 30, station: str | None = None) -> pd.DataFrame:
    """Event-weighted side calibration from logged signals with known outcomes.

    Fill-weighted reliability can make one station-day look like many
    independent forecasts. Here each ticker/side/bin contributes total weight
    1, no matter how many times the loop scored it.
    """
    return _df("""
    WITH signal_outcomes AS (
        SELECT km.station,
               km.ticker,
               s.side,
               CASE WHEN s.side = 'YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END AS p_side,
               CASE
                   WHEN truth.value_f IS NULL THEN NULL
                   WHEN (km.lower_f IS NULL OR truth.value_f >= km.lower_f)
                    AND (km.upper_f IS NULL OR truth.value_f < km.upper_f)
                   THEN CASE WHEN s.side = 'YES' THEN 1.0 ELSE 0.0 END
                   ELSE CASE WHEN s.side = 'YES' THEN 0.0 ELSE 1.0 END
               END AS won
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
          LEFT JOIN cli_obs c ON c.station = km.station AND c.local_date = km.valid_date
          LEFT JOIN daily_obs d ON d.station = km.station AND d.local_date = km.valid_date
          LEFT JOIN LATERAL (
              SELECT CASE
                       WHEN km.var = 'TMAX_DAILY' THEN COALESCE(c.tmax_f, d.tmax_f)
                       WHEN km.var = 'TMIN_DAILY' THEN COALESCE(c.tmin_f, d.tmin_f)
                       ELSE NULL
                     END AS value_f
          ) truth ON TRUE
         WHERE km.valid_date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
           AND s.fair_prob IS NOT NULL
           AND km.station IS NOT NULL
           AND km.valid_date IS NOT NULL
           AND km.var IN ('TMAX_DAILY', 'TMIN_DAILY')
           AND (%s::text IS NULL OR km.station = %s)
    ), binned AS (
        SELECT station,
               ticker,
               side,
               LEAST(10, GREATEST(1, WIDTH_BUCKET(p_side, 0, 1, 10))) AS bin,
               p_side,
               won
          FROM signal_outcomes
         WHERE won IS NOT NULL
    ), weighted AS (
        SELECT *,
               1.0 / COUNT(*) OVER (PARTITION BY ticker, side, bin) AS event_weight
          FROM binned
    )
    SELECT station,
           bin,
           SUM(p_side * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
           SUM(won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
           SUM(event_weight) AS n_events,
           COUNT(*) AS n_signals
      FROM weighted
     GROUP BY station, bin
    HAVING SUM(event_weight) >= 1
     ORDER BY station, bin
    """, (days_back, station, station))


def yes_probability_calibration(days_back: int = 60, n_bins: int = 10) -> pd.DataFrame:
    """Calibration of raw YES bucket probability vs actual bucket outcome.

    Unlike the side-probability reliability chart, this answers the core model
    question: when the CDF said a bucket had X% probability, how often did that
    exact bucket settle YES?
    """
    return _df("""
    WITH signal_outcomes AS (
        SELECT km.station,
               km.ticker,
               s.fair_prob AS p_yes,
               CASE
                   WHEN truth.value_f IS NULL THEN NULL
                   WHEN (km.lower_f IS NULL OR truth.value_f >= km.lower_f)
                    AND (km.upper_f IS NULL OR truth.value_f < km.upper_f)
                   THEN 1.0
                   ELSE 0.0
               END AS yes_won
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
          LEFT JOIN cli_obs c ON c.station = km.station AND c.local_date = km.valid_date
          LEFT JOIN daily_obs d ON d.station = km.station AND d.local_date = km.valid_date
          LEFT JOIN LATERAL (
              SELECT CASE
                       WHEN km.var = 'TMAX_DAILY' THEN COALESCE(c.tmax_f, d.tmax_f)
                       WHEN km.var = 'TMIN_DAILY' THEN COALESCE(c.tmin_f, d.tmin_f)
                       ELSE NULL
                     END AS value_f
          ) truth ON TRUE
         WHERE km.valid_date >= (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval
           AND s.fair_prob IS NOT NULL
           AND km.station IS NOT NULL
           AND km.valid_date IS NOT NULL
           AND km.var IN ('TMAX_DAILY', 'TMIN_DAILY')
    ), binned AS (
        SELECT station,
               ticker,
               LEAST(%s, GREATEST(1, WIDTH_BUCKET(p_yes, 0, 1, %s))) AS bin,
               p_yes,
               yes_won
          FROM signal_outcomes
         WHERE yes_won IS NOT NULL
    ), weighted AS (
        SELECT *,
               1.0 / COUNT(*) OVER (PARTITION BY ticker, bin) AS event_weight
          FROM binned
    ), station_bins AS (
        SELECT station,
               bin,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               SUM(yes_won * event_weight) AS n_yes
          FROM weighted
         GROUP BY station, bin
    ), global_bins AS (
        SELECT 'ALL'::text AS station,
               bin,
               SUM(event_weight) AS n,
               SUM(p_yes * event_weight) / NULLIF(SUM(event_weight), 0) AS mean_pred,
               SUM(yes_won * event_weight) / NULLIF(SUM(event_weight), 0) AS observed_freq,
               SUM(yes_won * event_weight) AS n_yes
          FROM weighted
         GROUP BY bin
    )
    SELECT * FROM global_bins
    UNION ALL
    SELECT * FROM station_bins
     ORDER BY station, bin
    """, (days_back, n_bins, n_bins))


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
    # Aggregate hourly TMP_2M into a daily MAX over the *station-local* day.
    # Plain valid_time::date uses DB session tz which is wrong for non-ET stations.
    from weather_bot.config import STATIONS
    tz = STATIONS[station].tz
    out["hrrr"] = _df("""
        SELECT MAX(value) AS tmax FROM det_forecast
         WHERE station=%s AND model='HRRR' AND var='TMP_2M'
           AND (valid_time AT TIME ZONE %s)::date = %s
           AND run_time = (SELECT MAX(run_time) FROM det_forecast
                            WHERE station=%s AND model='HRRR' AND var='TMP_2M'
                              AND (valid_time AT TIME ZONE %s)::date=%s)
    """, (station, tz, valid_date, station, tz, valid_date))
    return out


def kalshi_buckets_today(station: str, valid_date: date, var: str) -> pd.DataFrame:
    return _df("""
        SELECT ticker, lower_f, upper_f, status
          FROM kalshi_market
         WHERE station = %s AND valid_date = %s AND var = %s
         ORDER BY COALESCE(lower_f, -999), COALESCE(upper_f, 999)
    """, (station, valid_date, var))


def model_accuracy(days_back: int = 30) -> pd.DataFrame:
    """Per (station, model, valid_date) absolute error vs CLI ground truth.

    Models compared:
      - NBM:  prob_forecast percentile=50 (latest run for each valid_date)
      - HRRR: max(value) of det_forecast hourly TMP_2M for the valid_date
      - GFS/ECMWF: same shape as HRRR, model from det_forecast

    Truth = cli_obs.tmax_f (Kalshi NHIGH settlement source). Falls back to
    daily_obs.tmax_f only when CLI hasn't been captured yet (CLI is the more
    accurate source per the 2026-05-01 research comparison).

    Returns one row per (station, model, valid_date, predicted, truth, abs_err).
    """
    sql = """
    WITH truth AS (
        SELECT s.code AS station,
               d::date AS valid_date,
               COALESCE(c.tmax_f, dm.tmax_f) AS truth_tmax
          FROM stations s
          CROSS JOIN generate_series(
              ((now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval)::date,
              (now() AT TIME ZONE 'America/New_York')::date, '1 day'::interval) AS d
          LEFT JOIN cli_obs c ON c.station = s.code AND c.local_date = d::date
          LEFT JOIN daily_obs dm ON dm.station = s.code AND dm.local_date = d::date
         WHERE s.code = ANY(%s)
    ),
    nbm AS (
        SELECT pf.station, pf.valid_date, pf.value AS pred
          FROM prob_forecast pf
          JOIN (
              SELECT station, valid_date, MAX(run_time) AS rt
                FROM prob_forecast
               WHERE var='TMAX_DAILY' AND percentile=50
               GROUP BY station, valid_date
          ) lr ON lr.station = pf.station AND lr.valid_date = pf.valid_date AND lr.rt = pf.run_time
         WHERE pf.var='TMAX_DAILY' AND pf.percentile=50
    ),
    det AS (
        -- Group by station-local day, not DB session day, so KMDW/KDEN/KLAX
        -- don't mis-bucket boundary-hour temps. JOIN through stations for tz.
        SELECT df.station,
               (df.valid_time AT TIME ZONE st.tz)::date AS valid_date,
               df.model,
               MAX(df.value) AS pred
          FROM det_forecast df
          JOIN stations st ON st.code = df.station
          JOIN (
              SELECT df2.station, df2.model,
                     (df2.valid_time AT TIME ZONE st2.tz)::date AS vd,
                     MAX(df2.run_time) AS rt
                FROM det_forecast df2
                JOIN stations st2 ON st2.code = df2.station
               WHERE df2.model IN ('HRRR','GFS','ECMWF') AND df2.var='TMP_2M'
               GROUP BY df2.station, df2.model, (df2.valid_time AT TIME ZONE st2.tz)::date
          ) lr ON lr.station = df.station AND lr.model = df.model
              AND lr.vd = (df.valid_time AT TIME ZONE st.tz)::date AND lr.rt = df.run_time
         WHERE df.var = 'TMP_2M'
         GROUP BY df.station, (df.valid_time AT TIME ZONE st.tz)::date, df.model
    )
    SELECT t.station, t.valid_date, m.model, m.pred, t.truth_tmax,
           ABS(m.pred - t.truth_tmax) AS abs_err
      FROM truth t
      JOIN (
          SELECT station, valid_date, 'NBM' AS model, pred FROM nbm
          UNION ALL
          SELECT station, valid_date, model, pred FROM det
      ) m ON m.station = t.station AND m.valid_date = t.valid_date
     WHERE t.truth_tmax IS NOT NULL
     ORDER BY t.valid_date, t.station, m.model
    """
    from weather_bot.config import ACTIVE_FETCH_STATIONS
    return _df(sql, (days_back, ACTIVE_FETCH_STATIONS))


def vote_distribution_today() -> pd.DataFrame:
    """Today's signals grouped by model-vote tally (n_yes, n_no) and bot's chosen
    side. Powers the agreement-vs-action chart in the Calibration tab."""
    return _df("""
        SELECT side,
               (model_votes->>'n_yes')::int AS n_yes,
               (model_votes->>'n_no')::int AS n_no,
               action,
               COUNT(*) AS n
          FROM signal
         WHERE (ts AT TIME ZONE 'America/New_York')::date >= (now() AT TIME ZONE 'America/New_York')::date AND model_votes IS NOT NULL
         GROUP BY side, n_yes, n_no, action
         ORDER BY action, n_yes, n_no
    """)


def forecast_audit_log(station: str, valid_date) -> pd.DataFrame:
    """Chronological audit trail of every forecast issued for a (station, valid_date).

    Combines NBM p50, HRRR/GFS/ECMWF daily-MAX into one timeline so
    the user can see how predictions evolved leading up to the day. Inspired
    by dailydewpoint's "Detailed Forecast Log" panel — answers "did the model
    keep flipping its mind?" and shows when each source last agreed/disagreed.
    """
    from weather_bot.config import STATIONS
    tz = STATIONS[station].tz
    sql = """
    WITH nbm AS (
        SELECT 'NBM p50' AS source, run_time, value AS forecast_f
          FROM prob_forecast
         WHERE station=%s AND valid_date=%s AND var='TMAX_DAILY' AND percentile=50
    ),
    hrrr AS (
        SELECT 'HRRR' AS source, run_time, MAX(value) AS forecast_f
          FROM det_forecast
         WHERE station=%s AND model='HRRR' AND var='TMP_2M'
           AND (valid_time AT TIME ZONE %s)::date = %s
         GROUP BY run_time
    ),
    gfs AS (
        SELECT 'GFS' AS source, run_time, MAX(value) AS forecast_f
          FROM det_forecast
         WHERE station=%s AND model='GFS' AND var='TMP_2M'
           AND (valid_time AT TIME ZONE %s)::date = %s
         GROUP BY run_time
    ),
    ecmwf AS (
        SELECT 'ECMWF' AS source, run_time, MAX(value) AS forecast_f
          FROM det_forecast
         WHERE station=%s AND model='ECMWF' AND var='TMP_2M'
           AND (valid_time AT TIME ZONE %s)::date = %s
         GROUP BY run_time
    )
    SELECT * FROM nbm UNION ALL SELECT * FROM hrrr UNION ALL SELECT * FROM gfs UNION ALL SELECT * FROM ecmwf
     ORDER BY run_time, source
    """
    return _df(sql, (station, valid_date,
                       station, tz, valid_date,
                       station, tz, valid_date,
                       station, tz, valid_date))


def nws_overnight_jump(station: str, valid_date) -> dict | None:
    """How much did NBM revise the TMAX forecast for valid_date overnight?

    Compares the latest NBM p50 issued before valid_date 00:00 UTC to the
    earliest one issued at/after. Positive jump = NWS revised UP (typically
    means actual will overshoot the new forecast); negative = revised DOWN.

    Used as a reversal-risk signal — sharp overnight jumps are a "forecasters
    just learned something" warning that the bot's distribution may be lagging.
    """
    sql = """
    WITH nbm AS (
        SELECT run_time, value
          FROM prob_forecast
         WHERE station=%s AND valid_date=%s AND var='TMAX_DAILY' AND percentile=50
    ),
    yest AS (
        SELECT value AS v, run_time AS t FROM nbm
         WHERE run_time::date < %s
         ORDER BY run_time DESC LIMIT 1
    ),
    today AS (
        SELECT value AS v, run_time AS t FROM nbm
         WHERE run_time::date >= %s
         ORDER BY run_time ASC LIMIT 1
    )
    SELECT (SELECT v FROM yest) AS yesterday_last_f,
           (SELECT t FROM yest) AS yesterday_last_run,
           (SELECT v FROM today) AS today_first_f,
           (SELECT t FROM today) AS today_first_run
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, valid_date, valid_date, valid_date))
        r = cur.fetchone()
    if not r or r["yesterday_last_f"] is None or r["today_first_f"] is None:
        return None
    return {
        "station": station,
        "valid_date": valid_date.isoformat() if hasattr(valid_date, "isoformat") else str(valid_date),
        "yesterday_last_f": float(r["yesterday_last_f"]),
        "yesterday_last_run": r["yesterday_last_run"].isoformat(),
        "today_first_f": float(r["today_first_f"]),
        "today_first_run": r["today_first_run"].isoformat(),
        "jump_f": float(r["today_first_f"]) - float(r["yesterday_last_f"]),
    }


def temp_rate_of_change(
    station: str,
    lookback_hours: int = 2,
    min_span_minutes: int = 30,
    max_abs_rate_f_per_hr: float = 12.0,
) -> dict | None:
    """Rate of temperature change (°F/hour) at a station over the recent past.

    Used as a reversal-risk signal — fast warming late in the day suggests TMAX
    will overshoot the morning forecast; fast cooling suggests it won't recover.
    """
    sql = """
    SELECT temp_f, obs_time
      FROM metar_obs
     WHERE station = %s AND temp_f IS NOT NULL
       AND obs_time > now() - (%s || ' hours')::interval
     ORDER BY obs_time
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, lookback_hours + 0.5))   # small slack for boundary
        rows = cur.fetchall()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    dt_hours = (last["obs_time"] - first["obs_time"]).total_seconds() / 3600.0
    if dt_hours <= 0 or dt_hours * 60.0 < min_span_minutes:
        return None
    rate = (float(last["temp_f"]) - float(first["temp_f"])) / dt_hours
    if abs(rate) > max_abs_rate_f_per_hr:
        return {
            "station": station,
            "rate_f_per_hr": None,
            "first_temp_f": float(first["temp_f"]),
            "last_temp_f": float(last["temp_f"]),
            "first_time": first["obs_time"].isoformat(),
            "last_time": last["obs_time"].isoformat(),
            "n_obs": len(rows),
            "suppressed": True,
            "reason": f"implausible rate {rate:+.1f} F/hr",
        }
    return {
        "station": station,
        "rate_f_per_hr": rate,
        "first_temp_f": float(first["temp_f"]),
        "last_temp_f": float(last["temp_f"]),
        "first_time": first["obs_time"].isoformat(),
        "last_time": last["obs_time"].isoformat(),
        "n_obs": len(rows),
        "suppressed": False,
    }


def regional_temp_field(primary_station: str, lookback_min: int = 90) -> dict | None:
    """Wrapper exposing data/neighbor_obs.regional_field to the dashboard."""
    from weather_bot.data.neighbor_obs import regional_field
    return regional_field(primary_station, lookback_min=lookback_min)


def atmos_daily_features(station: str, target_date) -> dict | None:
    """Wrapper exposing data/atmos_fetcher.daily_features to the dashboard."""
    from weather_bot.data.atmos_fetcher import daily_features
    return daily_features(station, target_date)


def pnl_today() -> dict:
    """Today's P&L: realized settled fills + mark-to-market on open positions for valid_date=today."""
    settled = _df("""
        SELECT COALESCE(SUM({realized_pnl}), 0.0) AS net,
               COUNT(*) AS n
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = TRUE AND km.valid_date = (now() AT TIME ZONE 'America/New_York')::date
    """.format(realized_pnl=REALIZED_PNL_SQL))
    open_pos = _df("""
        SELECT pf.side, pf.price, pf.contracts, ms.yes_ask, ms.yes_bid
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          LEFT JOIN LATERAL (
              SELECT yes_ask, yes_bid FROM market_snapshot
               WHERE ticker = pf.ticker ORDER BY ts DESC LIMIT 1
          ) ms ON true
         WHERE pf.settled = FALSE AND km.valid_date = (now() AT TIME ZONE 'America/New_York')::date
    """)
    realized = float(settled.iloc[0]["net"]) if not settled.empty else 0.0
    n_settled = int(settled.iloc[0]["n"]) if not settled.empty else 0
    unrealized = 0.0
    for _, r in open_pos.iterrows():
        if pd.isna(r.get("yes_ask")) or pd.isna(r.get("yes_bid")):
            continue
        cur = float(r["yes_ask"]) if r["side"] == "YES" else (1.0 - float(r["yes_bid"]))
        unrealized += (cur - float(r["price"])) * int(r["contracts"])
    return {"realized": realized, "unrealized": unrealized,
            "net": realized + unrealized, "n_settled": n_settled, "n_open": len(open_pos)}


def pnl_yesterday() -> dict:
    """Yesterday's settled P&L (fills whose valid_date = yesterday and are settled)."""
    row = _df("""
        WITH fills AS (
            SELECT {realized_pnl} AS realized_pnl
              FROM paper_fill pf
              JOIN kalshi_market km ON km.ticker = pf.ticker
             WHERE pf.settled = TRUE AND km.valid_date = (now() AT TIME ZONE 'America/New_York')::date - INTERVAL '1 day'
        )
        SELECT COALESCE(SUM(realized_pnl), 0.0) AS net,
               COUNT(*) AS n_fills,
               COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 0) AS n_wins
          FROM fills
    """.format(realized_pnl=REALIZED_PNL_SQL))
    if row.empty or int(row.iloc[0]["n_fills"]) == 0:
        return {"net": None, "n_fills": 0, "n_wins": 0}
    return {
        "net": float(row.iloc[0]["net"]),
        "n_fills": int(row.iloc[0]["n_fills"]),
        "n_wins": int(row.iloc[0]["n_wins"]),
    }


def open_positions_with_obs() -> pd.DataFrame:
    """Open positions augmented with today's running obs and current p50 forecast."""
    return _df("""
        SELECT pf.id, pf.ts, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees,
               km.station, km.var, km.valid_date, km.lower_f, km.upper_f,
               (km.valid_date - (now() AT TIME ZONE 'America/New_York')::date)::int AS days_to_settle,
               ms.yes_ask, ms.yes_bid,
               obs.obs_tmax, obs.obs_tmin,
               fc.p50,
               cli.tmax_f AS cli_tmax, cli.tmin_f AS cli_tmin
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          JOIN stations st ON st.code = km.station
          LEFT JOIN LATERAL (
              SELECT yes_ask, yes_bid FROM market_snapshot
               WHERE ticker = pf.ticker ORDER BY ts DESC LIMIT 1
          ) ms ON true
          LEFT JOIN LATERAL (
              SELECT MAX(temp_f) AS obs_tmax, MIN(temp_f) AS obs_tmin
                FROM metar_obs
               WHERE station = km.station
                 AND (obs_time AT TIME ZONE st.tz)::date = km.valid_date
          ) obs ON true
          LEFT JOIN LATERAL (
              SELECT value AS p50
                FROM prob_forecast
               WHERE station = km.station AND valid_date = km.valid_date
                 AND var = km.var AND percentile = 50
                 AND run_time = (SELECT MAX(run_time) FROM prob_forecast
                                  WHERE station = km.station AND valid_date = km.valid_date
                                    AND var = km.var)
          ) fc ON true
          LEFT JOIN cli_obs cli
                 ON cli.station = km.station AND cli.local_date = km.valid_date
         WHERE pf.settled = FALSE
         ORDER BY km.valid_date, pf.ts
    """)


def guidance_source_health(hours: int = 24) -> pd.DataFrame:
    """Collector health for official guidance sources.

    One row per source with freshness, row volume, station coverage, and valid
    date span. This answers: "is the new research data arriving?"
    """
    return _df("""
        SELECT source,
               COUNT(*) AS rows,
               COUNT(DISTINCT station) AS stations,
               COUNT(DISTINCT station || ':' || valid_date::text) AS station_days,
               MAX(ingested_at) AS latest_ingest,
               ROUND(EXTRACT(EPOCH FROM (now() - MAX(ingested_at))) / 60.0, 1) AS lag_min,
               MIN(valid_date) AS min_valid_date,
               MAX(valid_date) AS max_valid_date
          FROM forecast_guidance
         WHERE ingested_at >= now() - (%s || ' hours')::interval
         GROUP BY source
         ORDER BY source
    """, (hours,))


def guidance_station_coverage(hours: int = 24) -> pd.DataFrame:
    """Station/source coverage matrix for recent official guidance rows."""
    return _df("""
        SELECT station,
               source,
               COUNT(*) AS rows,
               MAX(ingested_at) AS latest_ingest,
               ROUND(EXTRACT(EPOCH FROM (now() - MAX(ingested_at))) / 60.0, 1) AS lag_min,
               MIN(valid_date) AS min_valid_date,
               MAX(valid_date) AS max_valid_date
          FROM forecast_guidance
         WHERE ingested_at >= now() - (%s || ' hours')::interval
         GROUP BY station, source
         ORDER BY station, source
    """, (hours,))


def guidance_kalshi_coverage_gaps(hours: int = 3) -> pd.DataFrame:
    """Live Kalshi stations with missing recent official-guidance coverage."""
    return _df("""
    WITH kalshi AS (
        SELECT DISTINCT station
          FROM kalshi_market
         WHERE status IN ('open', 'active')
    ),
    guidance AS (
        SELECT station,
               BOOL_OR(source = 'NWS_GRID') AS has_nws_grid,
               BOOL_OR(source = 'NWS_PFM') AS has_pfm,
               BOOL_OR(source = 'LAMP') AS has_lamp,
               BOOL_OR(source = 'MAV') AS has_mav,
               BOOL_OR(source = 'OBS_TRACKER') AS has_obs_tracker,
               MAX(ingested_at) AS latest_ingest
          FROM forecast_guidance
         WHERE ingested_at >= now() - (%s || ' hours')::interval
         GROUP BY station
    )
    SELECT k.station,
           COALESCE(g.has_nws_grid, false) AS has_nws_grid,
           COALESCE(g.has_pfm, false) AS has_pfm,
           COALESCE(g.has_lamp, false) AS has_lamp,
           COALESCE(g.has_mav, false) AS has_mav,
           COALESCE(g.has_obs_tracker, false) AS has_obs_tracker,
           g.latest_ingest,
           CASE
             WHEN g.station IS NULL THEN 'NO_GUIDANCE'
             WHEN NOT COALESCE(g.has_nws_grid, false) THEN 'MISSING_NWS_GRID'
             WHEN NOT COALESCE(g.has_lamp, false) THEN 'MISSING_LAMP'
             WHEN NOT COALESCE(g.has_mav, false) THEN 'MISSING_MAV'
             ELSE 'OK'
           END AS status
      FROM kalshi k
      LEFT JOIN guidance g ON g.station = k.station
     ORDER BY status DESC, k.station
    """, (hours,))


def guidance_center_board(target_date, station_codes: list[str]) -> pd.DataFrame:
    """Current daily-high center board by station/source for one valid date."""
    if not station_codes:
        return pd.DataFrame()
    return _df("""
    WITH targets AS (
        SELECT UNNEST(%s::text[]) AS station
    ),
    nbm AS (
        SELECT pf.station, pf.value AS nbm_p50, pf.run_time AS nbm_run_time
          FROM prob_forecast pf
          JOIN (
              SELECT station, MAX(run_time) AS rt
                FROM prob_forecast
               WHERE valid_date = %s AND var = 'TMAX_DAILY' AND percentile = 50
               GROUP BY station
          ) lr ON lr.station = pf.station AND lr.rt = pf.run_time
         WHERE pf.valid_date = %s AND pf.var = 'TMAX_DAILY' AND pf.percentile = 50
    ),
    guidance_daily AS (
        SELECT fg.station, fg.source, fg.value, fg.run_time
          FROM forecast_guidance fg
          JOIN (
              SELECT station, source, MAX(run_time) AS rt
                FROM forecast_guidance
               WHERE valid_date = %s
                 AND var = 'TMAX_DAILY'
                 AND source IN ('NWS_GRID','NWS_PFM')
               GROUP BY station, source
          ) lr ON lr.station = fg.station AND lr.source = fg.source AND lr.rt = fg.run_time
         WHERE fg.valid_date = %s AND fg.var = 'TMAX_DAILY'
    ),
    guidance_hourly AS (
        SELECT fg.station, fg.source, MAX(fg.value) AS value, MAX(fg.run_time) AS run_time
          FROM forecast_guidance fg
          JOIN (
              SELECT station, source, MAX(run_time) AS rt
                FROM forecast_guidance
               WHERE valid_date = %s
                 AND var = 'TMP_2M'
                 AND source IN ('LAMP','MAV')
               GROUP BY station, source
          ) lr ON lr.station = fg.station AND lr.source = fg.source AND lr.rt = fg.run_time
         WHERE fg.valid_date = %s AND fg.var = 'TMP_2M'
         GROUP BY fg.station, fg.source
    ),
    wide_guidance AS (
        SELECT station,
               MAX(value) FILTER (WHERE source='NWS_GRID') AS nws_grid,
               MAX(run_time) FILTER (WHERE source='NWS_GRID') AS nws_grid_run,
               MAX(value) FILTER (WHERE source='NWS_PFM') AS pfm,
               MAX(run_time) FILTER (WHERE source='NWS_PFM') AS pfm_run,
               MAX(value) FILTER (WHERE source='LAMP') AS lamp,
               MAX(run_time) FILTER (WHERE source='LAMP') AS lamp_run,
               MAX(value) FILTER (WHERE source='MAV') AS mav,
               MAX(run_time) FILTER (WHERE source='MAV') AS mav_run
          FROM (
              SELECT * FROM guidance_daily
              UNION ALL
              SELECT * FROM guidance_hourly
          ) g
         GROUP BY station
    ),
    obs AS (
        SELECT m.station, MAX(m.temp_f) AS high_so_far
          FROM metar_obs m
          JOIN stations st ON st.code = m.station
         WHERE (m.obs_time AT TIME ZONE st.tz)::date = %s
         GROUP BY m.station
    ),
    truth AS (
        SELECT st.code AS station,
               COALESCE(c.tmax_f, d.tmax_f) AS truth_tmax
          FROM stations st
          LEFT JOIN cli_obs c ON c.station = st.code AND c.local_date = %s
          LEFT JOIN daily_obs d ON d.station = st.code AND d.local_date = %s
    )
    SELECT t.station,
           s.name,
           nbm.nbm_p50,
           wg.nws_grid,
           wg.pfm,
           wg.lamp,
           wg.mav,
           obs.high_so_far,
           truth.truth_tmax,
           src.spread_f,
           nbm.nbm_run_time,
           wg.nws_grid_run,
           wg.pfm_run,
           wg.lamp_run,
           wg.mav_run
      FROM targets t
      JOIN stations s ON s.code = t.station
      LEFT JOIN nbm ON nbm.station = t.station
      LEFT JOIN wide_guidance wg ON wg.station = t.station
      LEFT JOIN obs ON obs.station = t.station
      LEFT JOIN truth ON truth.station = t.station
      LEFT JOIN LATERAL (
          SELECT CASE WHEN COUNT(v) >= 2 THEN MAX(v) - MIN(v) END AS spread_f
            FROM (VALUES
                (nbm.nbm_p50),
                (wg.nws_grid),
                (wg.pfm),
                (wg.lamp),
                (wg.mav)
            ) vals(v)
           WHERE v IS NOT NULL
      ) src ON TRUE
     ORDER BY spread_f DESC NULLS LAST, t.station
    """, (
        station_codes, target_date, target_date,
        target_date, target_date,
        target_date, target_date,
        target_date, target_date, target_date,
    ))


def guidance_accuracy(days_back: int = 14) -> pd.DataFrame:
    """Recent center accuracy by source vs CLI/daily truth.

    Uses latest available run per source/station/valid_date. This is not the
    strict morning market-relative scorer; it is a collection monitor and early
    source-ranking view.
    """
    return _df("""
    WITH truth AS (
        SELECT s.code AS station,
               d::date AS valid_date,
               COALESCE(c.tmax_f, obs.tmax_f) AS truth_tmax
          FROM stations s
          CROSS JOIN generate_series(
              (now() AT TIME ZONE 'America/New_York')::date - (%s || ' days')::interval,
              (now() AT TIME ZONE 'America/New_York')::date - INTERVAL '1 day',
              INTERVAL '1 day'
          ) d
          LEFT JOIN cli_obs c ON c.station = s.code AND c.local_date = d::date
          LEFT JOIN daily_obs obs ON obs.station = s.code AND obs.local_date = d::date
         WHERE COALESCE(c.tmax_f, obs.tmax_f) IS NOT NULL
    ),
    nbm AS (
        SELECT pf.station, pf.valid_date, 'NBM'::text AS source, pf.value AS pred
          FROM prob_forecast pf
          JOIN (
              SELECT station, valid_date, MAX(run_time) AS rt
                FROM prob_forecast
               WHERE var='TMAX_DAILY' AND percentile=50
               GROUP BY station, valid_date
          ) lr ON lr.station=pf.station AND lr.valid_date=pf.valid_date AND lr.rt=pf.run_time
         WHERE pf.var='TMAX_DAILY' AND pf.percentile=50
    ),
    guidance_daily AS (
        SELECT fg.station, fg.valid_date, fg.source, fg.value AS pred
          FROM forecast_guidance fg
          JOIN (
              SELECT station, source, valid_date, MAX(run_time) AS rt
                FROM forecast_guidance
               WHERE var='TMAX_DAILY' AND source IN ('NWS_GRID','NWS_PFM')
               GROUP BY station, source, valid_date
          ) lr ON lr.station=fg.station AND lr.source=fg.source
              AND lr.valid_date=fg.valid_date AND lr.rt=fg.run_time
         WHERE fg.var='TMAX_DAILY'
    ),
    guidance_hourly AS (
        SELECT fg.station, fg.valid_date, fg.source, MAX(fg.value) AS pred
          FROM forecast_guidance fg
          JOIN (
              SELECT station, source, valid_date, MAX(run_time) AS rt
                FROM forecast_guidance
               WHERE var='TMP_2M' AND source IN ('LAMP','MAV')
               GROUP BY station, source, valid_date
          ) lr ON lr.station=fg.station AND lr.source=fg.source
              AND lr.valid_date=fg.valid_date AND lr.rt=fg.run_time
         WHERE fg.var='TMP_2M'
         GROUP BY fg.station, fg.valid_date, fg.source
    ),
    preds AS (
        SELECT * FROM nbm
        UNION ALL SELECT * FROM guidance_daily
        UNION ALL SELECT * FROM guidance_hourly
    )
    SELECT p.station,
           p.source,
           COUNT(*) AS n,
           ROUND(AVG(ABS(p.pred - t.truth_tmax))::numeric, 2) AS mae_f,
           ROUND(AVG(p.pred - t.truth_tmax)::numeric, 2) AS bias_f,
           ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(p.pred - t.truth_tmax))::numeric, 2) AS median_abs_err_f
      FROM preds p
      JOIN truth t ON t.station=p.station AND t.valid_date=p.valid_date
     GROUP BY p.station, p.source
     ORDER BY mae_f ASC, n DESC
    """, (days_back,))


def trade_eligible_stations() -> list[str]:
    from weather_bot.config import ACTIVE_TRADE_STATIONS
    return list(ACTIVE_TRADE_STATIONS)


def fetch_stations() -> list[str]:
    from weather_bot.config import ACTIVE_FETCH_STATIONS
    return list(ACTIVE_FETCH_STATIONS)


def neighbor_stations() -> list[str]:
    from weather_bot.config import NEIGHBOR_STATIONS
    codes: list[str] = []
    seen = set()
    for neighbors in NEIGHBOR_STATIONS.values():
        for station in neighbors:
            if station.code not in seen:
                codes.append(station.code)
                seen.add(station.code)
    return codes
