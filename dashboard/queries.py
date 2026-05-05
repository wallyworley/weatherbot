"""All SQL the dashboard runs. Single source of truth so the UI doesn't
sprout one-off queries scattered through it."""
from __future__ import annotations

from datetime import date, timedelta
import time

import pandas as pd

from weather_bot.data import persistence

_CACHE_TTL_SECONDS = 12
_DF_CACHE: dict[tuple[str, object], tuple[float, pd.DataFrame]] = {}


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
         WHERE station = 'GLOBAL' OR station = ANY(%s)
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
               s.edge, s.size_usd, s.action, s.skip_reason, s.model_votes,
               s.reversal_risk, s.notes,
               km.station, km.var, km.valid_date, km.lower_f, km.upper_f
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
         WHERE s.ts >= CURRENT_DATE
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
           AND ts >= CURRENT_DATE - (%s || ' days')::interval
         GROUP BY skip_reason
         ORDER BY n DESC
    """, (days_back,))


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
           AND km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
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
      - GFS:  same shape as HRRR, model='GFS'

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
              (CURRENT_DATE - (%s || ' days')::interval)::date,
              CURRENT_DATE, '1 day'::interval) AS d
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
               WHERE df2.model IN ('HRRR','GFS') AND df2.var='TMP_2M'
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
         WHERE ts >= CURRENT_DATE AND model_votes IS NOT NULL
         GROUP BY side, n_yes, n_no, action
         ORDER BY action, n_yes, n_no
    """)


def forecast_audit_log(station: str, valid_date) -> pd.DataFrame:
    """Chronological audit trail of every forecast issued for a (station, valid_date).

    Combines NBM p50, HRRR daily-MAX, and GFS daily-MAX into one timeline so
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
    )
    SELECT * FROM nbm UNION ALL SELECT * FROM hrrr UNION ALL SELECT * FROM gfs
     ORDER BY run_time, source
    """
    return _df(sql, (station, valid_date,
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


def temp_rate_of_change(station: str, lookback_hours: int = 2) -> dict | None:
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
    if dt_hours <= 0:
        return None
    return {
        "station": station,
        "rate_f_per_hr": (float(last["temp_f"]) - float(first["temp_f"])) / dt_hours,
        "first_temp_f": float(first["temp_f"]),
        "last_temp_f": float(last["temp_f"]),
        "first_time": first["obs_time"].isoformat(),
        "last_time": last["obs_time"].isoformat(),
        "n_obs": len(rows),
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
        SELECT COALESCE(SUM((pf.payout - pf.price) * pf.contracts - pf.fees), 0.0) AS net,
               COUNT(*) AS n
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = TRUE AND km.valid_date = CURRENT_DATE
    """)
    open_pos = _df("""
        SELECT pf.side, pf.price, pf.contracts, ms.yes_ask, ms.yes_bid
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          LEFT JOIN LATERAL (
              SELECT yes_ask, yes_bid FROM market_snapshot
               WHERE ticker = pf.ticker ORDER BY ts DESC LIMIT 1
          ) ms ON true
         WHERE pf.settled = FALSE AND km.valid_date = CURRENT_DATE
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
        SELECT COALESCE(SUM((pf.payout - pf.price) * pf.contracts - pf.fees), 0.0) AS net,
               COUNT(*) AS n_fills,
               COALESCE(SUM(CASE WHEN (pf.payout - pf.price) * pf.contracts - pf.fees > 0 THEN 1 ELSE 0 END), 0) AS n_wins
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = TRUE AND km.valid_date = CURRENT_DATE - INTERVAL '1 day'
    """)
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
               (km.valid_date - CURRENT_DATE)::int AS days_to_settle,
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


def trade_eligible_stations() -> list[str]:
    from weather_bot.config import ACTIVE_TRADE_STATIONS
    return list(ACTIVE_TRADE_STATIONS)


def fetch_stations() -> list[str]:
    from weather_bot.config import ACTIVE_FETCH_STATIONS
    return list(ACTIVE_FETCH_STATIONS)
