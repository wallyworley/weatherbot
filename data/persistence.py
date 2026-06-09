"""
Thin DB access layer — psycopg3, no ORM. Keep SQL visible.
"""
from __future__ import annotations

import json
import logging
import socket
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from weather_bot.config import DATABASE_URL, STATIONS

log = logging.getLogger(__name__)


@contextmanager
def connect():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False) as conn:
        yield conn


def _event_key(*parts) -> str:
    return "|".join("" if p is None else str(p) for p in parts)


def _model_run_source_type(model: str) -> str | None:
    return {
        "NBM_QMD": "nbm_run",
        "HRRR": "hrrr_run",
        "GFS": "gfs_run",
        "ECMWF": "ecmwf_run",
    }.get(str(model).upper())


def _json_ready(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(v) for v in value]
    return value


def record_info_provenance(rows: Iterable[dict]) -> None:
    """Insert first-seen research provenance, preserving the earliest sighting.

    EXP-2026-011 depends on genuine forward ``first_seen_at`` timestamps. Callers
    must not use this for historical backfills. Repeated polling/upserts keep the
    earliest first_seen_at for the stable (source_type, event_key).
    """
    prepared = []
    for row in rows:
        r = dict(row)
        if not r.get("source_type") or not r.get("event_key"):
            continue
        r.setdefault("first_seen_at", datetime.now(tz=timezone.utc))
        r.setdefault("ingest_host", socket.gethostname())
        summary = r.get("value_summary")
        if isinstance(summary, (dict, list, tuple, set)):
            r["value_summary"] = json.dumps(_json_ready(summary))
        elif summary is None:
            r["value_summary"] = None
        prepared.append(r)
    if not prepared:
        return

    sql = """
    INSERT INTO info_provenance
        (source_type, station, official_ts, event_key, first_seen_at, value_summary, ingest_host)
    VALUES
        (%(source_type)s, %(station)s, %(official_ts)s, %(event_key)s,
         %(first_seen_at)s, %(value_summary)s::jsonb, %(ingest_host)s)
    ON CONFLICT (source_type, event_key) DO UPDATE SET
        first_seen_at = LEAST(info_provenance.first_seen_at, EXCLUDED.first_seen_at),
        official_ts = COALESCE(info_provenance.official_ts, EXCLUDED.official_ts),
        value_summary = EXCLUDED.value_summary,
        ingest_host = EXCLUDED.ingest_host,
        updated_at = now()
    """
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.executemany(sql, prepared)
            conn.commit()
    except Exception as exc:
        log.warning("info_provenance write skipped: %s", exc)


def insert_kalshi_ws_book_events(rows: Iterable[dict]) -> None:
    """Research-only (EXP-2026-011 A5): high-cadence Kalshi WS book events. NOT read by any
    production probability/sizing/execution/gate path. Best-effort; never raises into the
    collector loop."""
    prepared = []
    for row in rows:
        r = dict(row)
        if not r.get("ticker") or not r.get("msg_type"):
            continue
        payload = r.get("payload")
        r["payload"] = json.dumps(_json_ready(payload)) if payload is not None else None
        prepared.append(r)
    if not prepared:
        return
    sql = """
    INSERT INTO kalshi_ws_book_event
        (ticker, msg_type, seq, exchange_ts, received_at, yes_bid, yes_ask, payload)
    VALUES
        (%(ticker)s, %(msg_type)s, %(seq)s, %(exchange_ts)s, %(received_at)s,
         %(yes_bid)s, %(yes_ask)s, %(payload)s::jsonb)
    """
    for r in prepared:
        r.setdefault("seq", None)
        r.setdefault("exchange_ts", None)
        r.setdefault("received_at", datetime.now(tz=timezone.utc))
        r.setdefault("yes_bid", None)
        r.setdefault("yes_ask", None)
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.executemany(sql, prepared)
            conn.commit()
    except Exception as exc:
        log.warning("kalshi_ws_book_event write skipped: %s", exc)


def active_weather_tickers() -> list[str]:
    """Active Kalshi weather market tickers from kalshi_market (for WS subscribe). Read-only."""
    sql = """
    SELECT ticker FROM kalshi_market
     WHERE status IN ('active', 'open')
       AND valid_date >= (now() AT TIME ZONE 'UTC')::date - 1
     ORDER BY ticker
    """
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return [r["ticker"] for r in cur.fetchall()]
    except Exception as exc:
        log.warning("active_weather_tickers query failed: %s", exc)
        return []


def _record_forecast_run_provenance(rows: list[dict]) -> None:
    by_event: dict[tuple, dict] = {}
    for row in rows:
        source_type = _model_run_source_type(str(row.get("model")))
        station = row.get("station")
        run_time = row.get("run_time")
        if not source_type or not station or not run_time:
            continue
        key = (source_type, station, run_time)
        item = by_event.setdefault(
            key,
            {
                "source_type": source_type,
                "station": station,
                "official_ts": run_time,
                "event_key": _event_key(station, row.get("model"), run_time),
                "value_summary": {
                    "model": row.get("model"),
                    "row_count": 0,
                    "vars": set(),
                    "valid_dates": set(),
                    "valid_times": 0,
                },
            },
        )
        summary = item["value_summary"]
        summary["row_count"] += 1
        if row.get("var") is not None:
            summary["vars"].add(row.get("var"))
        if row.get("valid_date") is not None:
            summary["valid_dates"].add(row.get("valid_date"))
        if row.get("valid_time") is not None:
            summary["valid_times"] += 1
    record_info_provenance(by_event.values())


def bootstrap_stations():
    """Seed the stations table from config (primaries + neighbors)."""
    from weather_bot.config import NEIGHBOR_STATIONS
    all_stations = list(STATIONS.values())
    seen_codes = {s.code for s in all_stations}
    for neighbors in NEIGHBOR_STATIONS.values():
        for s in neighbors:
            if s.code not in seen_codes:
                all_stations.append(s)
                seen_codes.add(s.code)
    with connect() as conn, conn.cursor() as cur:
        for s in all_stations:
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
def upsert_det_forecast(rows: Iterable[dict], record_provenance: bool = False):
    materialized = list(rows)
    sql = """
    INSERT INTO det_forecast(station, model, run_time, valid_time, lead_hr, var, value)
    VALUES (%(station)s, %(model)s, %(run_time)s, %(valid_time)s, %(lead_hr)s, %(var)s, %(value)s)
    ON CONFLICT (station, model, run_time, valid_time, var)
    DO NOTHING
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, materialized)
        conn.commit()
    if record_provenance:
        _record_forecast_run_provenance(materialized)


def upsert_prob_forecast(rows: Iterable[dict], record_provenance: bool = False):
    materialized = list(rows)
    sql = """
    INSERT INTO prob_forecast(station, model, run_time, valid_date, var, percentile, value)
    VALUES (%(station)s, %(model)s, %(run_time)s, %(valid_date)s, %(var)s, %(percentile)s, %(value)s)
    ON CONFLICT (station, model, run_time, valid_date, var, percentile)
    DO NOTHING
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, materialized)
        conn.commit()
    if record_provenance:
        _record_forecast_run_provenance(materialized)


def upsert_ensemble_forecast(rows: Iterable[dict]):
    sql = """
    INSERT INTO ensemble_forecast(station, model, run_time, valid_time, lead_hr, var, member, value)
    VALUES (%(station)s, %(model)s, %(run_time)s, %(valid_time)s, %(lead_hr)s,
            %(var)s, %(member)s, %(value)s)
    ON CONFLICT (station, model, run_time, valid_time, var, member)
    DO NOTHING
    """
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, list(rows))
        conn.commit()


def upsert_forecast_guidance(rows: Iterable[dict]):
    """Upsert official / station-specific forecast guidance rows.

    The table intentionally stores heterogeneous sources in one shape so the
    ablation harness can compare them as alternate forecast centers without
    giving any source special live-trading privileges.
    """
    sql = """
    INSERT INTO forecast_guidance
        (station, source, run_time, valid_time, valid_date, lead_hr, var, value, units, raw)
    VALUES
        (%(station)s, %(source)s, %(run_time)s, %(valid_time)s, %(valid_date)s,
         %(lead_hr)s, %(var)s, %(value)s, %(units)s, %(raw)s::jsonb)
    ON CONFLICT (station, source, run_time, valid_time, var) DO UPDATE SET
        valid_date = EXCLUDED.valid_date,
        lead_hr = EXCLUDED.lead_hr,
        value = EXCLUDED.value,
        units = EXCLUDED.units,
        raw = EXCLUDED.raw,
        ingested_at = now()
    """
    prepared = []
    for row in rows:
        r = dict(row)
        r.setdefault("units", "degF")
        r.setdefault("raw", None)
        if isinstance(r.get("raw"), (dict, list)):
            r["raw"] = json.dumps(r["raw"])
        prepared.append(r)
    if not prepared:
        return
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, prepared)
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
    return latest_det_tmax(station, valid_date, model="HRRR")


def latest_gfs_tmax(station: str, valid_date: date) -> float | None:
    return latest_det_tmax(station, valid_date, model="GFS")


def latest_ecmwf_tmax(station: str, valid_date: date) -> float | None:
    return latest_det_tmax(station, valid_date, model="ECMWF")


def latest_det_tmax(station: str, valid_date: date, model: str) -> float | None:
    """Latest daily TMAX from a deterministic model in det_forecast.

    Returns max of hourly TMP_2M from the latest run grouped by the
    *station-local* calendar day. Bare `valid_time::date` would group by the
    DB session timezone, which is wrong for stations not in that timezone
    (e.g., DB session=ET but station=KMDW which is CT).
    """
    tz = STATIONS[station].tz
    sql = """
    SELECT MAX(value) AS tmax
      FROM det_forecast
     WHERE station = %s AND model = %s AND var = 'TMP_2M'
       AND (valid_time AT TIME ZONE %s)::date = %s
       AND run_time = (
           SELECT MAX(run_time) FROM det_forecast
            WHERE station = %s AND model = %s AND var = 'TMP_2M'
              AND (valid_time AT TIME ZONE %s)::date = %s
       )
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, model, tz, valid_date, station, model, tz, valid_date))
        row = cur.fetchone()
        return row["tmax"] if row else None


# ---------------------------------------------------------------------------
# Point-in-time helpers for the replay harness.
# These mirror the latest_* helpers but constrain run_time <= as_of so the
# harness only uses forecasts available at the historical signal timestamp.
# ---------------------------------------------------------------------------
def nbm_percentiles_as_of(
    station: str, valid_date: date, as_of: datetime, var: str = "TMAX_DAILY"
) -> list[dict]:
    sql = """
    SELECT percentile, value, run_time
      FROM prob_forecast
     WHERE station = %s AND valid_date = %s AND var = %s
       AND run_time <= %s
       AND run_time = (
           SELECT MAX(run_time) FROM prob_forecast
            WHERE station = %s AND valid_date = %s AND var = %s
              AND run_time <= %s
       )
     ORDER BY percentile
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, valid_date, var, as_of, station, valid_date, var, as_of))
        return cur.fetchall()


def det_tmax_as_of(
    station: str, valid_date: date, model: str, as_of: datetime
) -> float | None:
    tz = STATIONS[station].tz
    sql = """
    SELECT MAX(value) AS tmax
      FROM det_forecast
     WHERE station = %s AND model = %s AND var = 'TMP_2M'
       AND (valid_time AT TIME ZONE %s)::date = %s
       AND run_time <= %s
       AND run_time = (
           SELECT MAX(run_time) FROM det_forecast
            WHERE station = %s AND model = %s AND var = 'TMP_2M'
              AND (valid_time AT TIME ZONE %s)::date = %s
              AND run_time <= %s
       )
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, model, tz, valid_date, as_of,
                          station, model, tz, valid_date, as_of))
        row = cur.fetchone()
        return row["tmax"] if row else None


def hrrr_tmax_as_of(station: str, valid_date: date, as_of: datetime) -> float | None:
    return det_tmax_as_of(station, valid_date, "HRRR", as_of)


def gfs_tmax_as_of(station: str, valid_date: date, as_of: datetime) -> float | None:
    return det_tmax_as_of(station, valid_date, "GFS", as_of)


def guidance_value_as_of(
    station: str,
    valid_date: date,
    source: str,
    var: str,
    as_of: datetime,
) -> float | None:
    """Latest daily guidance value available at `as_of` for a source/var."""
    sql = """
    SELECT value
      FROM forecast_guidance
     WHERE station = %s
       AND source = %s
       AND valid_date = %s
       AND var = %s
       AND run_time <= %s
     ORDER BY run_time DESC, valid_time DESC
     LIMIT 1
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, source, valid_date, var, as_of))
        row = cur.fetchone()
        return float(row["value"]) if row else None


def guidance_tmax_as_of(
    station: str,
    valid_date: date,
    source: str,
    as_of: datetime,
) -> float | None:
    """Latest TMAX center for a guidance source at `as_of`.

    Prefer explicit TMAX_DAILY rows. If a source only has hourly TMP_2M rows
    (for example LAMP text), reduce the latest run to the station-local daily
    maximum. OBS_TRACKER stores high-so-far under OBS_TMAX_SO_FAR and is not a
    forecast center, so callers should query that var directly when needed.
    """
    direct = guidance_value_as_of(station, valid_date, source, "TMAX_DAILY", as_of)
    if direct is not None:
        return direct
    tz = STATIONS[station].tz
    sql = """
    WITH latest AS (
        SELECT MAX(run_time) AS rt
          FROM forecast_guidance
         WHERE station = %s
           AND source = %s
           AND var = 'TMP_2M'
           AND (valid_time AT TIME ZONE %s)::date = %s
           AND run_time <= %s
    )
    SELECT MAX(value) AS tmax
      FROM forecast_guidance, latest
     WHERE station = %s
       AND source = %s
       AND var = 'TMP_2M'
       AND (valid_time AT TIME ZONE %s)::date = %s
       AND run_time = latest.rt
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, source, tz, valid_date, as_of,
                          station, source, tz, valid_date))
        row = cur.fetchone()
        return float(row["tmax"]) if row and row["tmax"] is not None else None


def station_bias_as_of(
    station: str, model: str, var: str, month: int, lead_day: int,
    as_of: datetime, cycle_hour: int | None = None,
) -> dict | None:
    """Return the most recent station_bias_history snapshot at or before
    `as_of.date()`. Falls back through cycle_hour → cycle-agnostic → None.
    Used by the PIT replay harness so historical signals are scored against
    the bias table that was actually live at the signal timestamp."""
    snap_date = as_of.date()

    def _q(cycle: int) -> dict | None:
        sql = """
        SELECT * FROM station_bias_history
         WHERE station=%s AND model=%s AND var=%s
           AND month=%s AND lead_day=%s AND cycle_hour=%s
           AND snapshot_date <= %s
         ORDER BY snapshot_date DESC
         LIMIT 1
        """
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql, (station, model, var, month, lead_day, cycle, snap_date))
            return cur.fetchone()

    if cycle_hour is not None and cycle_hour >= 0:
        row = _q(cycle_hour)
        if row is not None and int(row.get("sample_size") or 0) >= 8:
            return row
    return _q(-1)


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------
def upsert_metar(rows: Iterable[dict], record_provenance: bool = False):
    materialized = list(rows)
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
        cur.executemany(sql, materialized)
        conn.commit()
    if record_provenance:
        prov_rows = []
        for row in materialized:
            prov_rows.append(
                {
                    "source_type": "metar",
                    "station": row.get("station"),
                    "official_ts": row.get("obs_time"),
                    "event_key": _event_key(row.get("station"), row.get("obs_time")),
                    "value_summary": {
                        "temp_f": row.get("temp_f"),
                        "dewpoint_f": row.get("dewpoint_f"),
                        "wind_kt": row.get("wind_kt"),
                        "raw": row.get("raw"),
                    },
                }
            )
        record_info_provenance(prov_rows)


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
    """Upsert bias rows. `cycle_hour` defaults to -1 (cycle-agnostic legacy lane)
    when callers omit it; retrain writes both lanes explicitly."""
    sql = """
    INSERT INTO station_bias(station, model, var, month, lead_day, cycle_hour,
                             mean_bias_f, stddev_f, sample_size)
    VALUES (%(station)s, %(model)s, %(var)s, %(month)s, %(lead_day)s,
            %(cycle_hour)s, %(mean_bias_f)s, %(stddev_f)s, %(sample_size)s)
    ON CONFLICT (station, model, var, month, lead_day, cycle_hour) DO UPDATE
       SET mean_bias_f = EXCLUDED.mean_bias_f,
           stddev_f    = EXCLUDED.stddev_f,
           sample_size = EXCLUDED.sample_size,
           updated_at  = now()
    """
    materialized = [dict(r) for r in rows]
    for r in materialized:
        r.setdefault("cycle_hour", -1)
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, materialized)
        conn.commit()


def get_station_bias_exact(
    station: str, model: str, var: str, month: int, lead_day: int,
    cycle_hour: int = -1,
) -> dict | None:
    """Return bias row only if the exact (month, lead_day, cycle_hour) cell
    exists — no fallback. Defaults to cycle-agnostic lane (-1) for
    backwards compatibility with the calibration gate."""
    sql = """SELECT * FROM station_bias
              WHERE station=%s AND model=%s AND var=%s
                AND month=%s AND lead_day=%s AND cycle_hour=%s"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, model, var, month, lead_day, cycle_hour))
        return cur.fetchone()


_MIN_CYCLE_SPECIFIC_N = 8


def get_station_bias(
    station: str, model: str, var: str, month: int, lead_day: int,
    cycle_hour: int | None = None,
    min_cycle_specific_n: int = _MIN_CYCLE_SPECIFIC_N,
) -> dict | None:
    """Return bias row for the requested (month, lead_day), or fall back to
    the nearest available lead_day within the same regime.

    When `cycle_hour` is provided (0/6/12/18), the lookup first tries the
    cycle-specific row (cycle_hour=<n>). If absent or thin (sample_size <
    min_cycle_specific_n), it falls back to the cycle-agnostic row
    (cycle_hour=-1), then to the existing month/lead fallback chain. The
    cycle-agnostic fallback preserves pre-2026-05-17 behavior for callers
    that don't pass `cycle_hour`.

    Lead-0 boundary: lead_day=0 (same-day) MUST NOT fall back to lead>=1.
    Same-day forecasts are a distinct regime (current obs available, HRRR blend
    active, intraday persistence conditioning); lead-1+ residuals are
    uncorrelated with same-day residuals. Applying lead-1 bias to lead-0 caused
    the Apr 20 incident where a +4.3°F cold shift from lead-1 pushed p50 below
    the observed intraday floor.

    For lead>=1 queries, fallback is restricted to lead>=1 rows.
    """
    if cycle_hour is not None and cycle_hour >= 0:
        cycle_row = get_station_bias_exact(station, model, var, month, lead_day, cycle_hour)
        if cycle_row and int(cycle_row.get("sample_size") or 0) >= min_cycle_specific_n:
            return cycle_row
        # Fall through to cycle-agnostic / month / lead fallbacks below.

    sql_exact = """SELECT * FROM station_bias
                   WHERE station=%s AND model=%s AND var=%s
                     AND month=%s AND lead_day=%s AND cycle_hour=-1"""
    sql_nearest_lead = """
        SELECT * FROM station_bias
         WHERE station=%s AND model=%s AND var=%s AND month=%s AND lead_day >= 1
           AND cycle_hour=-1
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
           AND cycle_hour=-1
         ORDER BY month_dist ASC, sample_size DESC
         LIMIT 1
    """
    sql_nearest_month_any_lead = """
        SELECT *,
               LEAST(ABS(month - %s), 12 - ABS(month - %s)) AS month_dist
          FROM station_bias
         WHERE station=%s AND model=%s AND var=%s AND lead_day >= 1
           AND cycle_hour=-1
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
    import json
    sql = """
    INSERT INTO signal(ticker, side, fair_prob, market_ask, market_bid,
                       edge, ev_per_dollar, kelly_fraction, size_usd, action, notes,
                       skip_reason, model_votes, reversal_risk)
    VALUES (%(ticker)s, %(side)s, %(fair_prob)s, %(market_ask)s, %(market_bid)s,
            %(edge)s, %(ev_per_dollar)s, %(kelly_fraction)s, %(size_usd)s, %(action)s, %(notes)s,
            %(skip_reason)s, %(model_votes)s::jsonb, %(reversal_risk)s::jsonb)
    RETURNING id
    """
    mv = row.get("model_votes")
    rr = row.get("reversal_risk")
    row = {
        **row,
        "skip_reason": row.get("skip_reason"),
        "model_votes": json.dumps(mv) if mv is not None else None,
        "reversal_risk": json.dumps(rr) if rr is not None else None,
    }
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


def has_pending_paper_order(ticker: str, side: str) -> bool:
    sql = """SELECT 1 FROM paper_order
             WHERE ticker=%s AND side=%s AND status='PENDING' LIMIT 1"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker, side))
        return cur.fetchone() is not None


def insert_paper_order(row: dict) -> int:
    sql = """
    INSERT INTO paper_order(signal_id, ticker, side, limit_price, contracts,
                            fees_est, expires_at, notes)
    VALUES (%(signal_id)s, %(ticker)s, %(side)s, %(limit_price)s, %(contracts)s,
            %(fees_est)s, %(expires_at)s, %(notes)s)
    RETURNING id
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, row)
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id


def list_pending_paper_orders() -> list[dict]:
    sql = """
    SELECT id, signal_id, ticker, side, limit_price, contracts, fees_est,
           created_at, expires_at, notes
      FROM paper_order
     WHERE status = 'PENDING'
     ORDER BY created_at ASC
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def list_market_snapshots_for_order(ticker: str, created_at: datetime, expires_at: datetime) -> list[dict]:
    sql = """
    SELECT ts,
           yes_ask::float AS yes_ask,
           yes_bid::float AS yes_bid,
           yes_ask_size,
           yes_bid_size,
           no_ask::float AS no_ask,
           no_bid::float AS no_bid,
           no_ask_size,
           no_bid_size
      FROM market_snapshot
     WHERE ticker = %s
       AND ts > %s
       AND ts <= %s
     ORDER BY ts ASC
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker, created_at, expires_at))
        return cur.fetchall()


def mark_paper_order_filled(
    order_id: int,
    paper_fill_id: int,
    fill_price: float,
    fill_snapshot_ts: datetime,
) -> None:
    sql = """
    UPDATE paper_order
       SET status='FILLED',
           filled_at=now(),
           paper_fill_id=%s,
           fill_price=%s,
           fill_snapshot_ts=%s
     WHERE id=%s AND status='PENDING'
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (paper_fill_id, fill_price, fill_snapshot_ts, order_id))
        conn.commit()


def expire_pending_paper_orders() -> int:
    sql = """
    UPDATE paper_order
       SET status='EXPIRED'
     WHERE status='PENDING'
       AND expires_at <= now()
    RETURNING id
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        expired = cur.fetchall()
        conn.commit()
        return len(expired)


def list_unsettled_paper_fills() -> list[dict]:
    """Return unsettled fills joined with their market metadata (for settlement).

    Includes Kalshi's `expiration_value` when available — settle prefers this
    over our NWS CLI capture since Kalshi's value is the actual settlement
    authority. NULLIF on empty string handles still-open markets where the
    field exists but is blank.
    """
    sql = """
    SELECT pf.id, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees,
           km.station, km.var, km.valid_date, km.lower_f, km.upper_f,
           NULLIF(km.payload->>'expiration_value', '')::float AS kalshi_settle_value
      FROM paper_fill pf
      JOIN kalshi_market km ON km.ticker = pf.ticker
     WHERE pf.settled = FALSE
     ORDER BY km.valid_date ASC
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def settle_paper_fill(fill_id: int, payout: float) -> None:
    sql = """
    UPDATE paper_fill
       SET settled=TRUE,
           payout=%s,
           exit_price=NULL,
           exit_fees=0.0,
           exit_ts=NULL,
           exit_snapshot_ts=NULL,
           exit_reason=NULL
     WHERE id=%s
       AND exit_price IS NULL
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (payout, fill_id))
        conn.commit()


def close_paper_fill_early(
    fill_id: int,
    exit_price: float,
    exit_fees: float,
    exit_snapshot_ts: datetime | None,
    exit_reason: str = "TAKE_PROFIT",
) -> None:
    """Close a paper fill by selling it before settlement.

    `payout` remains NULL because it is reserved for final settlement outcome.
    """
    sql = """
    UPDATE paper_fill
       SET settled=TRUE,
           payout=NULL,
           exit_price=%s,
           exit_fees=%s,
           exit_ts=now(),
           exit_snapshot_ts=%s,
           exit_reason=%s
     WHERE id=%s
       AND settled=FALSE
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (exit_price, exit_fees, exit_snapshot_ts, exit_reason, fill_id))
        conn.commit()


# ---------------------------------------------------------------------------
# Market snapshots (orderbook top-of-book, logged every pull cycle)
# ---------------------------------------------------------------------------
def insert_market_snapshots(rows: Iterable[dict], record_provenance: bool = True) -> None:
    """Insert one snapshot row per market per pull cycle.

    Captures both YES and NO orderbook top-of-book — NO-side can diverge from
    (1 - yes_*) on low-volume markets due to fee-aware spread asymmetry,
    matters for backtest fidelity when bot trades the NO side.

    Uses ON CONFLICT DO NOTHING so re-running within the same second is safe.
    The (ticker, ts) primary key uses DEFAULT now() — caller does not supply ts.
    Missing NO-side fields default to NULL (back-compat with old callers).
    """
    sql = """
    INSERT INTO market_snapshot
        (ticker, yes_ask, yes_bid, yes_ask_size, yes_bid_size,
         no_ask, no_bid, no_ask_size, no_bid_size, status,
         last_price, volume_24h, open_interest)
    VALUES
        (%(ticker)s, %(yes_ask)s, %(yes_bid)s, %(yes_ask_size)s, %(yes_bid_size)s,
         %(no_ask)s, %(no_bid)s, %(no_ask_size)s, %(no_bid_size)s, %(status)s,
         %(last_price)s, %(volume_24h)s, %(open_interest)s)
    ON CONFLICT (ticker, ts) DO NOTHING
    """
    seen_at = datetime.now(tz=timezone.utc)
    rows_norm = [{**{"no_ask": None, "no_bid": None,
                      "no_ask_size": None, "no_bid_size": None,
                      "status": None}, **r}
                 for r in rows]
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, rows_norm)
        conn.commit()
    if record_provenance:
        record_info_provenance(
            {
                "source_type": "kalshi_book",
                "station": None,
                "official_ts": seen_at,
                "event_key": _event_key(row.get("ticker"), seen_at.isoformat()),
                "first_seen_at": seen_at,
                "value_summary": {
                    "ticker": row.get("ticker"),
                    "yes_ask": row.get("yes_ask"),
                    "yes_bid": row.get("yes_bid"),
                    "no_ask": row.get("no_ask"),
                    "no_bid": row.get("no_bid"),
                    "last_price": row.get("last_price"),
                    "status": row.get("status"),
                    "capture": "polling_interval_censored",
                },
            }
            for row in rows_norm
            if row.get("ticker")
        )


def insert_external_market_snapshots(rows: Iterable[dict], record_provenance: bool = True) -> None:
    seen_at = datetime.now(tz=timezone.utc)
    sql = """
    INSERT INTO external_market_snapshot
        (venue, event_slug, market_slug, question, station, valid_date,
         lower_f, upper_f, resolution_source, yes_token_id, no_token_id,
         yes_bid, yes_ask, yes_ask_size, no_bid, no_ask, no_ask_size,
         volume_24h, liquidity, payload)
    VALUES
        (%(venue)s, %(event_slug)s, %(market_slug)s, %(question)s, %(station)s, %(valid_date)s,
         %(lower_f)s, %(upper_f)s, %(resolution_source)s, %(yes_token_id)s, %(no_token_id)s,
         %(yes_bid)s, %(yes_ask)s, %(yes_ask_size)s, %(no_bid)s, %(no_ask)s, %(no_ask_size)s,
         %(volume_24h)s, %(liquidity)s, %(payload)s::jsonb)
    ON CONFLICT (venue, market_slug, ts) DO NOTHING
    """
    prepared = []
    for row in rows:
        r = dict(row)
        if isinstance(r.get("payload"), (dict, list)):
            r["payload"] = json.dumps(r["payload"])
        prepared.append(r)
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, prepared)
        conn.commit()
    if record_provenance:
        record_info_provenance(
            {
                "source_type": "polymarket_book",
                "station": row.get("station"),
                "official_ts": seen_at,
                "event_key": _event_key(row.get("venue"), row.get("market_slug"), seen_at.isoformat()),
                "first_seen_at": seen_at,
                "value_summary": {
                    "venue": row.get("venue"),
                    "event_slug": row.get("event_slug"),
                    "market_slug": row.get("market_slug"),
                    "valid_date": row.get("valid_date"),
                    "lower_f": row.get("lower_f"),
                    "upper_f": row.get("upper_f"),
                    "yes_bid": row.get("yes_bid"),
                    "yes_ask": row.get("yes_ask"),
                    "liquidity": row.get("liquidity"),
                    "capture": "polling_interval_censored",
                },
            }
            for row in prepared
            if row.get("market_slug")
        )
