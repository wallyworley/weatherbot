"""
Thin DB access layer — psycopg3, no ORM. Keep SQL visible.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from weather_bot.config import DATABASE_URL, STATIONS

log = logging.getLogger(__name__)


@contextmanager
def connect():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False) as conn:
        yield conn


def bootstrap_stations():
    """Seed the stations table from config."""
    with connect() as conn, conn.cursor() as cur:
        for s in STATIONS.values():
            cur.execute(
                """
                INSERT INTO stations(code, name, lat, lon, tz)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE
                   SET name=EXCLUDED.name, lat=EXCLUDED.lat,
                       lon=EXCLUDED.lon, tz=EXCLUDED.tz
                """,
                (s.code, s.name, s.lat, s.lon, s.tz),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------
def upsert_det_forecast(rows: Iterable[dict]):
    sql = """
    INSERT INTO det_forecast(station, model, run_time, valid_time, lead_hr, var, value)
    VALUES (%(station)s, %(model)s, %(run_time)s, %(valid_time)s, %(lead_hr)s, %(var)s, %(value)s)
    ON CONFLICT (station, model, run_time, valid_time, var)
    DO NOTHING
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, list(rows))
        conn.commit()


def upsert_prob_forecast(rows: Iterable[dict]):
    sql = """
    INSERT INTO prob_forecast(station, model, run_time, valid_date, var, percentile, value)
    VALUES (%(station)s, %(model)s, %(run_time)s, %(valid_date)s, %(var)s, %(percentile)s, %(value)s)
    ON CONFLICT (station, model, run_time, valid_date, var, percentile)
    DO NOTHING
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, list(rows))
        conn.commit()


def latest_nbm_percentiles(station: str, valid_date: date, var: str = "TMAX_DAILY") -> list[dict]:
    sql = """
    SELECT percentile, value, run_time
      FROM prob_forecast
     WHERE station = %s AND valid_date = %s AND var = %s
       AND run_time = (
           SELECT MAX(run_time) FROM prob_forecast
            WHERE station = %s AND valid_date = %s AND var = %s
       )
     ORDER BY percentile
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, valid_date, var, station, valid_date, var))
        return cur.fetchall()


def latest_hrrr_tmax(station: str, valid_date: date) -> float | None:
    """Get latest HRRR max TMP_2M for the local calendar day (approximated UTC range)."""
    sql = """
    SELECT MAX(value) AS tmax
      FROM det_forecast
     WHERE station = %s AND model = 'HRRR' AND var = 'TMP_2M'
       AND valid_time::date = %s
       AND run_time = (
           SELECT MAX(run_time) FROM det_forecast
            WHERE station = %s AND model = 'HRRR' AND var = 'TMP_2M'
              AND valid_time::date = %s
       )
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, valid_date, station, valid_date))
        row = cur.fetchone()
        return row["tmax"] if row else None


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------
def upsert_metar(rows: Iterable[dict]):
    sql = """
    INSERT INTO metar_obs(station, obs_time, temp_f, dewpoint_f, wind_kt, raw)
    VALUES (%(station)s, %(obs_time)s, %(temp_f)s, %(dewpoint_f)s, %(wind_kt)s, %(raw)s)
    ON CONFLICT (station, obs_time) DO UPDATE
       SET temp_f = EXCLUDED.temp_f,
           dewpoint_f = EXCLUDED.dewpoint_f,
           wind_kt = EXCLUDED.wind_kt,
           raw = EXCLUDED.raw
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, list(rows))
        conn.commit()


def upsert_daily_obs(rows: Iterable[dict]):
    sql = """
    INSERT INTO daily_obs(station, local_date, tmax_f, tmin_f, source)
    VALUES (%(station)s, %(local_date)s, %(tmax_f)s, %(tmin_f)s, %(source)s)
    ON CONFLICT (station, local_date) DO UPDATE
       SET tmax_f = EXCLUDED.tmax_f,
           tmin_f = EXCLUDED.tmin_f,
           source = EXCLUDED.source,
           updated_at = now()
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, list(rows))
        conn.commit()


def get_daily_obs(station: str, start: date, end: date) -> list[dict]:
    sql = """SELECT local_date, tmax_f, tmin_f FROM daily_obs
             WHERE station = %s AND local_date BETWEEN %s AND %s
             ORDER BY local_date"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, start, end))
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Bias / markets / signals
# ---------------------------------------------------------------------------
def upsert_station_bias(rows: Iterable[dict]):
    sql = """
    INSERT INTO station_bias(station, model, var, month, lead_day, mean_bias_f, stddev_f, sample_size)
    VALUES (%(station)s, %(model)s, %(var)s, %(month)s, %(lead_day)s,
            %(mean_bias_f)s, %(stddev_f)s, %(sample_size)s)
    ON CONFLICT (station, model, var, month, lead_day) DO UPDATE
       SET mean_bias_f = EXCLUDED.mean_bias_f,
           stddev_f    = EXCLUDED.stddev_f,
           sample_size = EXCLUDED.sample_size,
           updated_at  = now()
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, list(rows))
        conn.commit()


def get_station_bias(station: str, model: str, var: str, month: int, lead_day: int) -> dict | None:
    """Return bias row for the requested (month, lead_day), or fall back to
    the nearest available lead_day within the same regime.

    Lead-0 boundary: lead_day=0 (same-day) MUST NOT fall back to lead>=1.
    Same-day forecasts are a distinct regime (current obs available, HRRR blend
    active, intraday persistence conditioning); lead-1+ residuals are
    uncorrelated with same-day residuals. Applying lead-1 bias to lead-0 caused
    the Apr 20 incident where a +4.3°F cold shift from lead-1 pushed p50 below
    the observed intraday floor.

    For lead>=1 queries, fallback is restricted to lead>=1 rows.
    """
    sql_exact = """SELECT * FROM station_bias
                   WHERE station=%s AND model=%s AND var=%s AND month=%s AND lead_day=%s"""
    sql_nearest_lead = """
        SELECT * FROM station_bias
         WHERE station=%s AND model=%s AND var=%s AND month=%s AND lead_day >= 1
         ORDER BY ABS(lead_day - %s) ASC, lead_day ASC
         LIMIT 1
    """
    # Month-fallback: when the exact month has no rows (typical on the 1st
    # day of the month before that month's first nightly retrain), fall back
    # to the nearest month in the calendar by absolute distance, using a
    # cyclic 12-month metric. The lead-0 boundary still applies.
    sql_nearest_month_exact_lead = """
        SELECT *,
               LEAST(ABS(month - %s), 12 - ABS(month - %s)) AS month_dist
          FROM station_bias
         WHERE station=%s AND model=%s AND var=%s AND lead_day=%s
         ORDER BY month_dist ASC, sample_size DESC
         LIMIT 1
    """
    sql_nearest_month_any_lead = """
        SELECT *,
               LEAST(ABS(month - %s), 12 - ABS(month - %s)) AS month_dist
          FROM station_bias
         WHERE station=%s AND model=%s AND var=%s AND lead_day >= 1
         ORDER BY month_dist ASC, ABS(lead_day - %s) ASC, lead_day ASC
         LIMIT 1
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql_exact, (station, model, var, month, lead_day))
        row = cur.fetchone()
        if row:
            return row
        # Same-month, nearest-lead fallback (preserves lead-0 boundary).
        if lead_day != 0:
            cur.execute(sql_nearest_lead, (station, model, var, month, lead_day))
            row = cur.fetchone()
            if row:
                return row
        # Cross-month fallback. lead-0 is preserved at lead-0; lead>=1 may
        # match nearest available lead in the chosen month.
        if lead_day == 0:
            cur.execute(sql_nearest_month_exact_lead, (month, month, station, model, var, lead_day))
            return cur.fetchone()
        cur.execute(sql_nearest_month_any_lead, (month, month, station, model, var, lead_day))
        return cur.fetchone()


def upsert_kalshi_market(rows: Iterable[dict]):
    sql = """
    INSERT INTO kalshi_market(ticker, event_ticker, station, var, valid_date,
                              lower_f, upper_f, status, payload)
    VALUES (%(ticker)s, %(event_ticker)s, %(station)s, %(var)s, %(valid_date)s,
            %(lower_f)s, %(upper_f)s, %(status)s, %(payload)s)
    ON CONFLICT (ticker) DO UPDATE
       SET lower_f    = EXCLUDED.lower_f,
           upper_f    = EXCLUDED.upper_f,
           status     = EXCLUDED.status,
           payload    = EXCLUDED.payload,
           updated_at = now()
    """
    prepared = []
    for r in rows:
        r = dict(r)
        if isinstance(r.get("payload"), (dict, list)):
            r["payload"] = json.dumps(r["payload"])
        prepared.append(r)
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, prepared)
        conn.commit()


def insert_signal(row: dict) -> int:
    sql = """
    INSERT INTO signal(ticker, side, fair_prob, market_ask, market_bid,
                       edge, ev_per_dollar, kelly_fraction, size_usd, action, notes)
    VALUES (%(ticker)s, %(side)s, %(fair_prob)s, %(market_ask)s, %(market_bid)s,
            %(edge)s, %(ev_per_dollar)s, %(kelly_fraction)s, %(size_usd)s, %(action)s, %(notes)s)
    RETURNING id
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, row)
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id


# ---------------------------------------------------------------------------
# Paper trading
# ---------------------------------------------------------------------------
def has_open_paper_fill(ticker: str, side: str) -> bool:
    """True if we already have an unsettled paper fill for this (ticker, side).

    Prevents the orchestrator from double-filling when it runs every 15 min.
    """
    sql = """SELECT 1 FROM paper_fill
             WHERE ticker=%s AND side=%s AND settled=FALSE LIMIT 1"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker, side))
        return cur.fetchone() is not None


def insert_paper_fill(row: dict) -> int:
    sql = """
    INSERT INTO paper_fill(signal_id, ticker, side, price, contracts, fees, settled, payout)
    VALUES (%(signal_id)s, %(ticker)s, %(side)s, %(price)s, %(contracts)s, %(fees)s, FALSE, NULL)
    RETURNING id
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, row)
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id


def list_unsettled_paper_fills() -> list[dict]:
    """Return unsettled fills joined with their market metadata (for settlement)."""
    sql = """
    SELECT pf.id, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees,
           km.station, km.var, km.valid_date, km.lower_f, km.upper_f
      FROM paper_fill pf
      JOIN kalshi_market km ON km.ticker = pf.ticker
     WHERE pf.settled = FALSE
     ORDER BY km.valid_date ASC
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def settle_paper_fill(fill_id: int, payout: float) -> None:
    sql = "UPDATE paper_fill SET settled=TRUE, payout=%s WHERE id=%s"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (payout, fill_id))
        conn.commit()


# ---------------------------------------------------------------------------
# Market snapshots (orderbook top-of-book, logged every pull cycle)
# ---------------------------------------------------------------------------
def insert_market_snapshots(rows: Iterable[dict]) -> None:
    """Insert one snapshot row per market per pull cycle.

    Uses ON CONFLICT DO NOTHING so re-running within the same second is safe.
    The (ticker, ts) primary key uses DEFAULT now() — caller does not supply ts.
    """
    sql = """
    INSERT INTO market_snapshot
        (ticker, yes_ask, yes_bid, yes_ask_size, yes_bid_size,
         last_price, volume_24h, open_interest)
    VALUES
        (%(ticker)s, %(yes_ask)s, %(yes_bid)s, %(yes_ask_size)s, %(yes_bid_size)s,
         %(last_price)s, %(volume_24h)s, %(open_interest)s)
    ON CONFLICT (ticker, ts) DO NOTHING
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, list(rows))
        conn.commit()
