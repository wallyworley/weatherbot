"""Market-information forensics dataset.

Builds a research-only station/date/snapshot dataset for asking why Kalshi's
daily-temperature market becomes accurate near settlement. This module reads
stored observations, guidance, WeatherBot signals, and Kalshi orderbook
snapshots. It does not import trading logic and does not write to production
tables.

Run real reports on the VPS where the authoritative PostgreSQL database lives.
Do not SSH/tunnel data back to a local machine for evidence collection. Local
runs are code smoke tests only unless explicitly working from a restored
research copy that is labeled as such.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from weather_bot.config import ACTIVE_FETCH_STATIONS, STATIONS
from weather_bot.data import persistence


INTERVALS_MIN = (1, 5, 10, 30, 60)
MATERIAL_CENTER_MOVE_F = 0.10


@dataclass(frozen=True)
class BucketTick:
    station: str
    valid_date: date
    var: str
    event_ticker: str
    ticker: str
    snapshot_ts: datetime
    lead_day: int
    lower_f: float | None
    upper_f: float | None
    market_p: float | None
    weatherbot_p: float | None
    signal_ts: datetime | None
    live_metar_max_f: float | None
    latest_metar_ts: datetime | None
    cli_tmax_f: float | None
    cli_issued_at: datetime | None
    nbm_percentiles: dict | None
    det_centers: dict | None
    guidance_values: dict | None
    past_market_p: dict[int, float | None]
    future_market_p: dict[int, float | None]


@dataclass(frozen=True)
class ForensicRow:
    station: str
    valid_date: date
    snapshot_ts_utc: datetime
    local_time: str
    lead_day: int
    var: str
    event_ticker: str
    bucket_count: int
    bucket_set_json: str
    market_probs_json: str
    market_raw_prob_sum: float | None
    market_center_f: float | None
    market_spread_f: float | None
    weatherbot_probs_json: str
    weatherbot_raw_prob_sum: float | None
    weatherbot_center_f: float | None
    weatherbot_spread_f: float | None
    weatherbot_missing_buckets: int
    live_metar_max_f: float | None
    latest_metar_ts_utc: datetime | None
    latest_metar_age_min: float | None
    cli_available: bool
    cli_tmax_f: float | None
    cli_issued_at_utc: datetime | None
    dsm_available: bool
    dsm_tmax_f: float | None
    final_cli_settlement_f: float | None
    live_metar_boundary_distance_f: float | None
    live_metar_bucket: str
    remaining_heating_climo_mean_f: float | None
    remaining_heating_climo_p50_f: float | None
    remaining_heating_climo_prob_gt_0_5f: float | None
    remaining_heating_climo_n: int
    model_centers_json: str
    market_center_move_1m_f: float | None
    market_center_move_5m_f: float | None
    market_center_move_10m_f: float | None
    market_center_move_30m_f: float | None
    market_center_move_60m_f: float | None
    market_center_move_next_1m_f: float | None
    market_center_move_next_5m_f: float | None
    market_center_move_next_10m_f: float | None
    market_center_move_next_30m_f: float | None
    market_center_move_next_60m_f: float | None
    market_move_vs_latest_metar: str


def _clamp_prob(p: float | None) -> float | None:
    if p is None:
        return None
    return min(1.0, max(0.0, float(p)))


def _normalize(values: Iterable[float | None]) -> list[float] | None:
    clean = [_clamp_prob(v) for v in values]
    if any(v is None for v in clean):
        return None
    probs = [float(v) for v in clean if v is not None]
    total = sum(probs)
    if total <= 0:
        return None
    return [p / total for p in probs]


def _bucket_label(lower_f: float | None, upper_f: float | None) -> str:
    if lower_f is None and upper_f is None:
        return "all"
    if lower_f is None:
        return f"<{upper_f:g}"
    if upper_f is None:
        return f">={lower_f:g}"
    return f"{lower_f:g}-{upper_f:g}"


def _typical_width(rows: list[BucketTick]) -> float:
    widths = [
        float(r.upper_f) - float(r.lower_f)
        for r in rows
        if r.lower_f is not None and r.upper_f is not None and r.upper_f > r.lower_f
    ]
    return statistics.median(widths) if widths else 1.0


def _bucket_value(row: BucketTick, width: float) -> float | None:
    if row.lower_f is not None and row.upper_f is not None:
        return (float(row.lower_f) + float(row.upper_f)) / 2.0
    if row.lower_f is None and row.upper_f is not None:
        return float(row.upper_f) - width / 2.0
    if row.lower_f is not None and row.upper_f is None:
        return float(row.lower_f) + width / 2.0
    return None


def _weighted_center(values: list[float], probs: list[float]) -> float:
    return sum(v * p for v, p in zip(values, probs))


def _weighted_spread(values: list[float], probs: list[float]) -> float:
    center = _weighted_center(values, probs)
    var = sum(p * (v - center) ** 2 for v, p in zip(values, probs))
    return math.sqrt(max(0.0, var))


def _center_and_spread(rows: list[BucketTick], attr: str) -> tuple[float | None, float | None, float | None]:
    width = _typical_width(rows)
    values = [_bucket_value(row, width) for row in rows]
    if any(v is None for v in values):
        return None, None, None
    raw_probs = [getattr(row, attr) for row in rows]
    probs = _normalize(raw_probs)
    raw_sum = None
    if all(p is not None for p in raw_probs):
        raw_sum = sum(float(p) for p in raw_probs if p is not None)
    if probs is None:
        return None, None, raw_sum
    vals = [float(v) for v in values if v is not None]
    return _weighted_center(vals, probs), _weighted_spread(vals, probs), raw_sum


def _center_from_prob_map(rows: list[BucketTick], probs_by_ticker: dict[str, float | None]) -> float | None:
    width = _typical_width(rows)
    values: list[float] = []
    raw_probs: list[float | None] = []
    for row in rows:
        value = _bucket_value(row, width)
        if value is None:
            return None
        values.append(value)
        raw_probs.append(probs_by_ticker.get(row.ticker))
    probs = _normalize(raw_probs)
    if probs is None:
        return None
    return _weighted_center(values, probs)


def _window_start(ts: datetime, minutes: int) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    secs = int((ts.astimezone(timezone.utc) - epoch).total_seconds())
    win = minutes * 60
    return epoch + timedelta(seconds=(secs // win) * win)


def coherent_snapshot_groups(
    ticks: list[BucketTick], tick_minutes: int = 10, min_buckets: int = 3
) -> list[list[BucketTick]]:
    """Return every coherent station/date/window snapshot."""
    groups: dict[tuple, dict[str, BucketTick]] = defaultdict(dict)
    for tick in ticks:
        win = _window_start(tick.snapshot_ts, tick_minutes)
        key = (tick.station, tick.valid_date, tick.var, tick.event_ticker, tick.lead_day, win)
        existing = groups[key].get(tick.ticker)
        if existing is None or tick.snapshot_ts > existing.snapshot_ts:
            groups[key][tick.ticker] = tick

    out = [list(v.values()) for _, v in sorted(groups.items()) if len(v) >= min_buckets]
    return [sorted(rows, key=lambda r: (float("-inf") if r.lower_f is None else r.lower_f, r.ticker)) for rows in out]


def _boundary_distance(live_metar_max_f: float | None, rows: list[BucketTick]) -> tuple[float | None, str]:
    if live_metar_max_f is None:
        return None, "no_live_metar"
    boundaries: list[float] = []
    containing = "outside"
    for row in rows:
        if row.lower_f is not None:
            boundaries.append(float(row.lower_f))
        if row.upper_f is not None:
            boundaries.append(float(row.upper_f))
        lower_ok = row.lower_f is None or live_metar_max_f >= row.lower_f
        upper_ok = row.upper_f is None or live_metar_max_f < row.upper_f
        if lower_ok and upper_ok:
            containing = _bucket_label(row.lower_f, row.upper_f)
    if not boundaries:
        return None, containing
    return min(abs(live_metar_max_f - b) for b in boundaries), containing


def _classify_market_vs_metar(
    latest_metar_age_min: float | None,
    move_10m: float | None,
    next_move_10m: float | None,
    threshold_f: float = MATERIAL_CENTER_MOVE_F,
) -> str:
    """Conservative timing label, not a causality claim."""
    if latest_metar_age_min is None:
        return "no_live_metar"
    before = abs(move_10m) >= threshold_f if move_10m is not None else False
    after = abs(next_move_10m) >= threshold_f if next_move_10m is not None else False
    if latest_metar_age_min <= 10:
        if after and not before:
            return "market_moves_after_recent_metar"
        if before and not after:
            return "market_moved_before_or_at_recent_metar"
        if before and after:
            return "market_moved_around_recent_metar"
        return "no_material_move_after_recent_metar"
    if latest_metar_age_min <= 60:
        return "market_already_repriced_after_metar" if before else "recent_metar_no_material_market_move"
    return "no_recent_metar"


def _json_dumps(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _read_dsm_longitudinal(path: Path) -> dict[tuple[str, date], float]:
    if not path.exists():
        return {}
    out: dict[tuple[str, date], float] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            if row.get("source") != "DSM":
                continue
            tmax = row.get("tmax_f")
            if tmax not in (None, ""):
                out[(row["station"], date.fromisoformat(row["data_date"]))] = float(tmax)
    return out


def _station_tz(station: str) -> str:
    return STATIONS[station].tz


def _local_dt(station: str, ts: datetime) -> datetime:
    return ts.astimezone(ZoneInfo(_station_tz(station)))


def _remaining_heating_climo(
    station: str,
    valid_date: date,
    local_snapshot_time: time,
    lookback_days: int = 60,
) -> tuple[float | None, float | None, float | None, int]:
    sql = """
    WITH hist AS (
        SELECT d::date AS local_date
          FROM generate_series(
               %(valid_date)s::date - (%(lookback_days)s || ' days')::interval,
               %(valid_date)s::date - interval '1 day',
               interval '1 day'
          ) AS d
    ),
    before_obs AS (
        SELECT h.local_date, MAX(m.temp_f)::float AS max_so_far
          FROM hist h
          LEFT JOIN metar_obs m
            ON m.station = %(station)s
           AND (m.obs_time AT TIME ZONE %(tz)s)::date = h.local_date
           AND (m.obs_time AT TIME ZONE %(tz)s)::time <= %(local_time)s::time
         GROUP BY h.local_date
    ),
    full_metar AS (
        SELECT h.local_date, MAX(m.temp_f)::float AS metar_tmax
          FROM hist h
          LEFT JOIN metar_obs m
            ON m.station = %(station)s
           AND (m.obs_time AT TIME ZONE %(tz)s)::date = h.local_date
         GROUP BY h.local_date
    ),
    finals AS (
        SELECT h.local_date,
               COALESCE(c.tmax_f::float, d.tmax_f::float, fm.metar_tmax) AS final_tmax
          FROM hist h
          LEFT JOIN cli_obs c ON c.station = %(station)s AND c.local_date = h.local_date
          LEFT JOIN daily_obs d ON d.station = %(station)s AND d.local_date = h.local_date
          LEFT JOIN full_metar fm ON fm.local_date = h.local_date
    )
    SELECT (f.final_tmax - b.max_so_far)::float AS remaining
      FROM finals f
      JOIN before_obs b ON b.local_date = f.local_date
     WHERE f.final_tmax IS NOT NULL
       AND b.max_so_far IS NOT NULL
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "station": station,
                "valid_date": valid_date,
                "lookback_days": lookback_days,
                "tz": _station_tz(station),
                "local_time": local_snapshot_time,
            },
        )
        vals = [float(r["remaining"]) for r in cur.fetchall()]
    if not vals:
        return None, None, None, 0
    return statistics.fmean(vals), statistics.median(vals), sum(1 for v in vals if v > 0.5) / len(vals), len(vals)


def collect_bucket_ticks(
    days_back: int,
    max_lead_day: int,
    var: str,
    stations: list[str],
    include_current: bool = False,
    tick_minutes: int = 10,
    min_buckets: int = 3,
    explain: bool = False,
) -> list[BucketTick]:
    current_op = "<=" if include_current else "<"
    # Performance: reduce to coherent-snapshot REPRESENTATIVES (latest snapshot per
    # ticker per tick-window) in SQL FIRST, then enrich only those with the 15 LATERAL
    # subqueries. Previously every raw snapshot was enriched before the Python
    # window-dedup, so cost scaled with all snapshots (~10x+ waste). The window here
    # matches _window_start (epoch floor to tick_minutes).
    sql = f"""
    WITH base AS (
        SELECT ms.ticker,
               ms.ts AS snapshot_ts,
               ((ms.yes_ask::float + ms.yes_bid::float) / 2.0) AS market_p,
               km.event_ticker,
               km.station,
               km.valid_date,
               km.var,
               km.lower_f::float AS lower_f,
               km.upper_f::float AS upper_f,
               st.tz AS tz,
               GREATEST(0, (km.valid_date - (ms.ts AT TIME ZONE st.tz)::date)::int) AS lead_day,
               to_timestamp(floor(extract(epoch FROM ms.ts) / %(win_sec)s) * %(win_sec)s) AS win
          FROM market_snapshot ms
          JOIN kalshi_market km ON km.ticker = ms.ticker
          JOIN stations st ON st.code = km.station
         WHERE km.station = ANY(%(stations)s)
           AND km.var = %(var)s
           AND km.valid_date >= CURRENT_DATE - (%(days_back)s || ' days')::interval
           AND km.valid_date {current_op} CURRENT_DATE
           AND ms.ts >= CURRENT_DATE - ((%(days_back)s + 3) || ' days')::interval
           AND ms.yes_ask IS NOT NULL
           AND ms.yes_bid IS NOT NULL
           AND GREATEST(0, (km.valid_date - (ms.ts AT TIME ZONE st.tz)::date)::int)
               BETWEEN 0 AND %(max_lead_day)s
    ),
    eligible AS (
        SELECT station, valid_date, var, event_ticker, lead_day, win
          FROM base
         GROUP BY station, valid_date, var, event_ticker, lead_day, win
        HAVING COUNT(DISTINCT ticker) >= %(min_buckets)s
    ),
    reps AS (
        SELECT DISTINCT ON (b.station, b.valid_date, b.var, b.event_ticker, b.lead_day, b.ticker, b.win)
               b.ticker, b.snapshot_ts, b.market_p, b.event_ticker, b.station, b.valid_date, b.var,
               b.lower_f, b.upper_f, b.tz, b.lead_day
          FROM base b
          JOIN eligible e ON e.station = b.station AND e.valid_date = b.valid_date
                         AND e.var = b.var AND e.event_ticker = b.event_ticker
                         AND e.lead_day = b.lead_day AND e.win = b.win
         ORDER BY b.station, b.valid_date, b.var, b.event_ticker, b.lead_day, b.ticker, b.win, b.snapshot_ts DESC
    )
    SELECT reps.ticker,
           reps.snapshot_ts,
           reps.market_p,
           reps.event_ticker,
           reps.station,
           reps.valid_date,
           reps.var,
           reps.lower_f,
           reps.upper_f,
           reps.lead_day,
           sig.fair_prob::float AS weatherbot_p,
           sig.ts AS signal_ts,
           met.live_metar_max_f,
           met.latest_metar_ts,
           cli.tmax_f::float AS cli_tmax_f,
           cli.issued_at AS cli_issued_at,
           nbm.percentiles AS nbm_percentiles,
           det.centers AS det_centers,
           guidance.values AS guidance_values,
           p1.market_p AS p1_market_p,
           p5.market_p AS p5_market_p,
           p10.market_p AS p10_market_p,
           p30.market_p AS p30_market_p,
           p60.market_p AS p60_market_p,
           f1.market_p AS f1_market_p,
           f5.market_p AS f5_market_p,
           f10.market_p AS f10_market_p,
           f30.market_p AS f30_market_p,
           f60.market_p AS f60_market_p
      FROM reps
      LEFT JOIN LATERAL (
          SELECT s.fair_prob, s.ts
            FROM signal s
           WHERE s.ticker = reps.ticker
             AND s.ts <= reps.snapshot_ts
           ORDER BY s.ts DESC
           LIMIT 1
      ) sig ON true
      LEFT JOIN LATERAL (
          SELECT MAX(m.temp_f)::float AS live_metar_max_f,
                 MAX(m.obs_time) AS latest_metar_ts
            FROM metar_obs m
           WHERE m.station = reps.station
             AND m.obs_time >= (reps.valid_date::timestamp AT TIME ZONE reps.tz)
             AND m.obs_time <= reps.snapshot_ts
      ) met ON true
      LEFT JOIN cli_obs cli ON cli.station = reps.station AND cli.local_date = reps.valid_date
      LEFT JOIN LATERAL (
          SELECT jsonb_object_agg(percentile::text, value ORDER BY percentile) AS percentiles
            FROM prob_forecast pf
           WHERE pf.station = reps.station
             AND pf.valid_date = reps.valid_date
             AND pf.var = reps.var
             AND pf.run_time = (
                 SELECT MAX(pf2.run_time)
                   FROM prob_forecast pf2
                  WHERE pf2.station = reps.station
                    AND pf2.valid_date = reps.valid_date
                    AND pf2.var = reps.var
                    AND pf2.run_time <= reps.snapshot_ts
             )
      ) nbm ON true
      LEFT JOIN LATERAL (
          -- Evidence (EXPLAIN ANALYZE 2026-06-07): the previous form ran a correlated
          -- MAX(run_time) SubPlan per candidate det_forecast row (1.665M executions,
          -- ~405 s). Restructured: iterate the 3 models explicitly so the "latest run
          -- with day coverage" is found ONCE per (rep, model) via ORDER BY run_time DESC
          -- LIMIT 1 over an indexed run_time range, then MAX the day's values for that run.
          SELECT jsonb_object_agg(model, tmax) AS centers
            FROM (
                SELECT mm.model,
                       (SELECT MAX(d.value)::float
                          FROM det_forecast d
                         WHERE d.station = reps.station
                           AND d.model = mm.model
                           AND d.var = 'TMP_2M'
                           AND d.valid_time >= (reps.valid_date::timestamp AT TIME ZONE reps.tz)
                           AND d.valid_time <  ((reps.valid_date + 1)::timestamp AT TIME ZONE reps.tz)
                           AND d.run_time = (
                               SELECT d2.run_time
                                 FROM det_forecast d2
                                WHERE d2.station = reps.station
                                  AND d2.model = mm.model
                                  AND d2.var = 'TMP_2M'
                                  AND d2.valid_time >= (reps.valid_date::timestamp AT TIME ZONE reps.tz)
                                  AND d2.valid_time <  ((reps.valid_date + 1)::timestamp AT TIME ZONE reps.tz)
                                  AND d2.run_time <= reps.snapshot_ts
                                  AND d2.run_time >= reps.snapshot_ts - interval '2 days'
                                ORDER BY d2.run_time DESC
                                LIMIT 1
                           )) AS tmax
                  FROM (VALUES ('HRRR'), ('GFS'), ('ECMWF')) mm(model)
            ) d
           WHERE d.tmax IS NOT NULL
      ) det ON true
      LEFT JOIN LATERAL (
          SELECT jsonb_object_agg(key, value) AS values
            FROM (
                SELECT DISTINCT ON (fg.source, fg.var)
                       fg.source || ':' || fg.var AS key,
                       fg.value::float AS value
                  FROM forecast_guidance fg
                 WHERE fg.station = reps.station
                   AND fg.valid_date = reps.valid_date
                   AND fg.run_time <= reps.snapshot_ts
                 ORDER BY fg.source, fg.var, fg.run_time DESC, fg.valid_time DESC
            ) g
      ) guidance ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts <= reps.snapshot_ts - interval '1 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts DESC LIMIT 1
      ) p1 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts <= reps.snapshot_ts - interval '5 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts DESC LIMIT 1
      ) p5 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts <= reps.snapshot_ts - interval '10 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts DESC LIMIT 1
      ) p10 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts <= reps.snapshot_ts - interval '30 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts DESC LIMIT 1
      ) p30 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts <= reps.snapshot_ts - interval '60 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts DESC LIMIT 1
      ) p60 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts >= reps.snapshot_ts + interval '1 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts ASC LIMIT 1
      ) f1 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts >= reps.snapshot_ts + interval '5 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts ASC LIMIT 1
      ) f5 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts >= reps.snapshot_ts + interval '10 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts ASC LIMIT 1
      ) f10 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts >= reps.snapshot_ts + interval '30 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts ASC LIMIT 1
      ) f30 ON true
      LEFT JOIN LATERAL (
          SELECT ((p.yes_ask::float + p.yes_bid::float) / 2.0) AS market_p
            FROM market_snapshot p
           WHERE p.ticker = reps.ticker AND p.ts >= reps.snapshot_ts + interval '60 minutes'
             AND p.yes_ask IS NOT NULL AND p.yes_bid IS NOT NULL
           ORDER BY p.ts ASC LIMIT 1
      ) f60 ON true
     ORDER BY reps.station, reps.valid_date, reps.snapshot_ts, reps.lower_f NULLS FIRST
    """
    params = {"days_back": days_back, "max_lead_day": max_lead_day, "var": var,
              "stations": stations, "win_sec": tick_minutes * 60, "min_buckets": min_buckets}
    with persistence.connect() as conn, conn.cursor() as cur:
        if explain:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
            for row in cur.fetchall():
                print(row.get("QUERY PLAN") if hasattr(row, "get") else row[0])
            return []
        cur.execute(sql, params)
        rows = cur.fetchall()

    out: list[BucketTick] = []
    for r in rows:
        past = {i: None if r[f"p{i}_market_p"] is None else float(r[f"p{i}_market_p"]) for i in INTERVALS_MIN}
        future = {i: None if r[f"f{i}_market_p"] is None else float(r[f"f{i}_market_p"]) for i in INTERVALS_MIN}
        out.append(
            BucketTick(
                station=r["station"],
                valid_date=r["valid_date"],
                var=r["var"],
                event_ticker=r["event_ticker"],
                ticker=r["ticker"],
                snapshot_ts=r["snapshot_ts"],
                lead_day=int(r["lead_day"]),
                lower_f=None if r["lower_f"] is None else float(r["lower_f"]),
                upper_f=None if r["upper_f"] is None else float(r["upper_f"]),
                market_p=None if r["market_p"] is None else float(r["market_p"]),
                weatherbot_p=None if r["weatherbot_p"] is None else float(r["weatherbot_p"]),
                signal_ts=r["signal_ts"],
                live_metar_max_f=None if r["live_metar_max_f"] is None else float(r["live_metar_max_f"]),
                latest_metar_ts=r["latest_metar_ts"],
                cli_tmax_f=None if r["cli_tmax_f"] is None else float(r["cli_tmax_f"]),
                cli_issued_at=r["cli_issued_at"],
                nbm_percentiles=r["nbm_percentiles"],
                det_centers=r["det_centers"],
                guidance_values=r["guidance_values"],
                past_market_p=past,
                future_market_p=future,
            )
        )
    return out


def _prob_map(rows: list[BucketTick], attr: str) -> dict[str, float | None]:
    probs = _normalize(getattr(row, attr) for row in rows)
    if probs is None:
        return {row.ticker: None for row in rows}
    return {row.ticker: probs[i] for i, row in enumerate(rows)}


def _movement(rows: list[BucketTick], interval_min: int, direction: str, current_center: float | None) -> float | None:
    if current_center is None:
        return None
    source = "past_market_p" if direction == "past" else "future_market_p"
    probs = {row.ticker: getattr(row, source).get(interval_min) for row in rows}
    center = _center_from_prob_map(rows, probs)
    if center is None:
        return None
    return current_center - center if direction == "past" else center - current_center


def _model_centers(rows: list[BucketTick]) -> dict[str, float]:
    first = rows[0]
    centers: dict[str, float] = {}
    if first.nbm_percentiles:
        for pct, value in first.nbm_percentiles.items():
            centers[f"NBM_P{pct}"] = float(value)
    if first.det_centers:
        for model, value in first.det_centers.items():
            centers[str(model)] = float(value)
    if first.guidance_values:
        for key, value in first.guidance_values.items():
            centers[str(key)] = float(value)
    return centers


def build_rows(
    groups: list[list[BucketTick]],
    dsm_values: dict[tuple[str, date], float],
    climo_lookback_days: int = 60,
    limit_groups: int | None = None,
    climo_round_min: int = 10,
) -> list[ForensicRow]:
    climo_cache: dict[tuple[str, date, time], tuple[float | None, float | None, float | None, int]] = {}
    selected = groups[:limit_groups] if limit_groups else groups
    out: list[ForensicRow] = []
    for rows in selected:
        first = rows[0]
        snapshot_ts = max(row.snapshot_ts for row in rows)
        local_dt = _local_dt(first.station, snapshot_ts)
        market_center, market_spread, market_raw_sum = _center_and_spread(rows, "market_p")
        weatherbot_center, weatherbot_spread, weatherbot_raw_sum = _center_and_spread(rows, "weatherbot_p")
        boundary_distance, live_bucket = _boundary_distance(first.live_metar_max_f, rows)
        latest_metar_age = None
        if first.latest_metar_ts is not None:
            latest_metar_age = (snapshot_ts - first.latest_metar_ts).total_seconds() / 60.0

        # Floor the local snapshot time to climo_round_min so all snapshots in the
        # same station/day/window share one climatology query (large cache win; the
        # remaining-heating climo is insensitive to a few minutes).
        _floored_min = ((local_dt.hour * 60 + local_dt.minute) // climo_round_min) * climo_round_min
        climo_time = time(_floored_min // 60, _floored_min % 60)
        climo_key = (first.station, first.valid_date, climo_time)
        if climo_key not in climo_cache:
            climo_cache[climo_key] = _remaining_heating_climo(
                first.station,
                first.valid_date,
                climo_time,
                lookback_days=climo_lookback_days,
            )
        climo_mean, climo_p50, climo_prob, climo_n = climo_cache[climo_key]
        moves_past = {i: _movement(rows, i, "past", market_center) for i in INTERVALS_MIN}
        moves_next = {i: _movement(rows, i, "future", market_center) for i in INTERVALS_MIN}

        bucket_set = [
            {"ticker": row.ticker, "label": _bucket_label(row.lower_f, row.upper_f), "lower_f": row.lower_f, "upper_f": row.upper_f}
            for row in rows
        ]
        dsm_tmax = dsm_values.get((first.station, first.valid_date))
        out.append(
            ForensicRow(
                station=first.station,
                valid_date=first.valid_date,
                snapshot_ts_utc=snapshot_ts,
                local_time=local_dt.isoformat(),
                lead_day=first.lead_day,
                var=first.var,
                event_ticker=first.event_ticker,
                bucket_count=len(rows),
                bucket_set_json=_json_dumps(bucket_set),
                market_probs_json=_json_dumps(_prob_map(rows, "market_p")),
                market_raw_prob_sum=market_raw_sum,
                market_center_f=market_center,
                market_spread_f=market_spread,
                weatherbot_probs_json=_json_dumps(_prob_map(rows, "weatherbot_p")),
                weatherbot_raw_prob_sum=weatherbot_raw_sum,
                weatherbot_center_f=weatherbot_center,
                weatherbot_spread_f=weatherbot_spread,
                weatherbot_missing_buckets=sum(1 for row in rows if row.weatherbot_p is None),
                live_metar_max_f=first.live_metar_max_f,
                latest_metar_ts_utc=first.latest_metar_ts,
                latest_metar_age_min=latest_metar_age,
                cli_available=first.cli_tmax_f is not None,
                cli_tmax_f=first.cli_tmax_f,
                cli_issued_at_utc=first.cli_issued_at,
                dsm_available=dsm_tmax is not None,
                dsm_tmax_f=dsm_tmax,
                final_cli_settlement_f=first.cli_tmax_f,
                live_metar_boundary_distance_f=boundary_distance,
                live_metar_bucket=live_bucket,
                remaining_heating_climo_mean_f=climo_mean,
                remaining_heating_climo_p50_f=climo_p50,
                remaining_heating_climo_prob_gt_0_5f=climo_prob,
                remaining_heating_climo_n=climo_n,
                model_centers_json=_json_dumps(_model_centers(rows)),
                market_center_move_1m_f=moves_past[1],
                market_center_move_5m_f=moves_past[5],
                market_center_move_10m_f=moves_past[10],
                market_center_move_30m_f=moves_past[30],
                market_center_move_60m_f=moves_past[60],
                market_center_move_next_1m_f=moves_next[1],
                market_center_move_next_5m_f=moves_next[5],
                market_center_move_next_10m_f=moves_next[10],
                market_center_move_next_30m_f=moves_next[30],
                market_center_move_next_60m_f=moves_next[60],
                market_move_vs_latest_metar=_classify_market_vs_metar(latest_metar_age, moves_past[10], moves_next[10]),
            )
        )
    return out


def write_csv(rows: list[ForensicRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _avg(values: Iterable[float | None]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.fmean(vals) if vals else None


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _age_bucket(minutes: float | None) -> str:
    if minutes is None:
        return "none"
    if minutes <= 5:
        return "0-5m"
    if minutes <= 10:
        return "5-10m"
    if minutes <= 30:
        return "10-30m"
    if minutes <= 60:
        return "30-60m"
    return "60m+"


def _boundary_bucket(distance: float | None) -> str:
    if distance is None:
        return "none"
    if distance <= 0.25:
        return "<=0.25F"
    if distance <= 0.5:
        return "0.25-0.5F"
    if distance <= 1.0:
        return "0.5-1.0F"
    return ">1.0F"


def _summary_line(label: str, rows: list[ForensicRow]) -> str:
    return (
        f"| {label} | {len(rows)} | "
        f"{_fmt(_avg(r.latest_metar_age_min for r in rows), 1)} | "
        f"{_fmt(_avg(abs(r.market_center_move_10m_f) for r in rows if r.market_center_move_10m_f is not None), 3)} | "
        f"{_fmt(_avg(abs(r.market_center_move_next_10m_f) for r in rows if r.market_center_move_next_10m_f is not None), 3)} | "
        f"{sum(1 for r in rows if r.live_metar_boundary_distance_f is not None and r.live_metar_boundary_distance_f <= 0.5)} | "
        f"{sum(1 for r in rows if r.cli_available)} | "
        f"{sum(1 for r in rows if r.dsm_available)} |"
    )


def render_markdown(rows: list[ForensicRow], days_back: int, include_current: bool) -> str:
    station_groups: dict[str, list[ForensicRow]] = defaultdict(list)
    age_groups: dict[str, list[ForensicRow]] = defaultdict(list)
    boundary_groups: dict[str, list[ForensicRow]] = defaultdict(list)
    timing_groups: dict[str, list[ForensicRow]] = defaultdict(list)
    for row in rows:
        station_groups[row.station].append(row)
        age_groups[_age_bucket(row.latest_metar_age_min)].append(row)
        boundary_groups[_boundary_bucket(row.live_metar_boundary_distance_f)].append(row)
        timing_groups[row.market_move_vs_latest_metar].append(row)

    lines = [
        f"# Market Information Forensics - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Window: last {days_back} days; current valid date included: `{include_current}`.",
        "",
        "Research-only. The authoritative run target is the VPS PostgreSQL database.",
        "",
        "## Coverage",
        "",
        f"- snapshot rows: {len(rows)}",
        f"- station-days: {len({(r.station, r.valid_date) for r in rows})}",
        f"- stations: {len(station_groups)}",
        "",
        "## Station Evidence",
        "",
        "| station | snapshots | avg METAR age min | abs 10m move | abs next 10m move | boundary <=0.5F | CLI rows | DSM rows |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for station, vals in sorted(station_groups.items()):
        lines.append(_summary_line(station, vals))

    lines.extend([
        "",
        "## Latency: Latest METAR Age",
        "",
        "| latest METAR age | snapshots | avg METAR age min | abs 10m move | abs next 10m move | boundary <=0.5F | CLI rows | DSM rows |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for label in ["0-5m", "5-10m", "10-30m", "30-60m", "60m+", "none"]:
        if label in age_groups:
            lines.append(_summary_line(label, age_groups[label]))

    lines.extend([
        "",
        "## Boundary States",
        "",
        "| distance to boundary | snapshots | avg METAR age min | abs 10m move | abs next 10m move | boundary <=0.5F | CLI rows | DSM rows |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for label in ["<=0.25F", "0.25-0.5F", "0.5-1.0F", ">1.0F", "none"]:
        if label in boundary_groups:
            lines.append(_summary_line(label, boundary_groups[label]))

    lines.extend([
        "",
        "## Timing Labels",
        "",
        "| label | snapshots | avg METAR age min | abs 10m move | abs next 10m move | boundary <=0.5F | CLI rows | DSM rows |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for label, vals in sorted(timing_groups.items()):
        lines.append(_summary_line(label, vals))

    lines.extend([
        "",
        "## Current Recommendation",
        "",
        "No paper-only candidate signal is promoted by this report alone. Use the CSV to pre-register a latency or boundary-state signal, then test it out of sample against market-relative Brier and RPS with paired confidence intervals.",
    ])
    return "\n".join(lines) + "\n"


def run(
    days_back: int = 30,
    max_lead_day: int = 1,
    var: str = "TMAX_DAILY",
    stations: list[str] | None = None,
    out_dir: Path = Path("research/reports"),
    tick_minutes: int = 10,
    min_buckets: int = 3,
    include_current: bool = False,
    climo_lookback_days: int = 60,
    dsm_longitudinal: Path = Path("research/reports/longitudinal.csv"),
    limit_groups: int | None = None,
    explain: bool = False,
) -> dict:
    stations = stations or list(ACTIVE_FETCH_STATIONS)
    if explain:
        collect_bucket_ticks(days_back, max_lead_day, var, stations, include_current,
                             tick_minutes=tick_minutes, min_buckets=min_buckets, explain=True)
        return {"explain": True}
    ticks = collect_bucket_ticks(days_back, max_lead_day, var, stations, include_current,
                                 tick_minutes=tick_minutes, min_buckets=min_buckets)
    groups = coherent_snapshot_groups(ticks, tick_minutes=tick_minutes, min_buckets=min_buckets)
    rows = build_rows(
        groups,
        dsm_values=_read_dsm_longitudinal(dsm_longitudinal),
        climo_lookback_days=climo_lookback_days,
        limit_groups=limit_groups,
        climo_round_min=tick_minutes,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"market_information_forensics_{date.today()}"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    write_csv(rows, csv_path)
    md_path.write_text(render_markdown(rows, days_back=days_back, include_current=include_current))
    return {"bucket_ticks": len(ticks), "snapshot_groups": len(groups), "rows": len(rows), "csv_path": str(csv_path), "report_path": str(md_path)}


def _parse_stations(value: str) -> list[str] | None:
    vals = [s.strip().upper() for s in value.split(",") if s.strip()]
    return vals or None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--max-lead-day", type=int, default=1)
    parser.add_argument("--var", default="TMAX_DAILY")
    parser.add_argument("--stations", default="", help="Comma-separated station codes. Defaults to ACTIVE_FETCH_STATIONS.")
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    parser.add_argument("--tick-minutes", type=int, default=10)
    parser.add_argument("--min-buckets", type=int, default=3)
    parser.add_argument("--include-current", action="store_true")
    parser.add_argument("--climo-lookback-days", type=int, default=60)
    parser.add_argument("--dsm-longitudinal", type=Path, default=Path("research/reports/longitudinal.csv"))
    parser.add_argument("--limit-groups", type=int, default=None)
    parser.add_argument("--explain", action="store_true", help="EXPLAIN ANALYZE the collect query and exit.")
    args = parser.parse_args()
    result = run(
        days_back=args.days_back,
        max_lead_day=args.max_lead_day,
        var=args.var,
        stations=_parse_stations(args.stations),
        out_dir=args.out_dir,
        tick_minutes=args.tick_minutes,
        min_buckets=args.min_buckets,
        include_current=args.include_current,
        climo_lookback_days=args.climo_lookback_days,
        dsm_longitudinal=args.dsm_longitudinal,
        limit_groups=args.limit_groups,
        explain=args.explain,
    )
    if not args.explain:
        print(Path(result["report_path"]).read_text())
