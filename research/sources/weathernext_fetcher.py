"""WeatherNext 2 BigQuery adapter for forecast benchmarking.

WeatherNext access is account/project dependent: Google requires a data request
and Analytics Hub subscription before a BigQuery table is available. This module
therefore fails softly by default, but once `WEATHERNEXT_BQ_TABLE` is configured
it can query 2m temperature ensemble members and aggregate daily TMAX/TMIN.
"""
from __future__ import annotations

import os
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from weather_bot.config import STATIONS


class WeatherNextUnavailable(RuntimeError):
    """WeatherNext cannot be queried in the current environment."""


@dataclass(frozen=True)
class WeatherNextHour:
    valid_time: datetime
    ensemble_member: str
    temp_f: float


def _k_to_f(k: float) -> float:
    return (float(k) - 273.15) * 9.0 / 5.0 + 32.0


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    vals = sorted(values)
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def resolve_table(table: str | None = None) -> str:
    """Return the fully qualified WeatherNext 2 BigQuery table name."""
    table = table or os.getenv("WEATHERNEXT_BQ_TABLE")
    if table:
        return table

    project = os.getenv("WEATHERNEXT_BQ_PROJECT")
    dataset = os.getenv("WEATHERNEXT_BQ_DATASET")
    table_name = os.getenv("WEATHERNEXT_BQ_TABLE_NAME", "weathernext_2_0_0")
    if project and dataset:
        return f"{project}.{dataset}.{table_name}"

    raise WeatherNextUnavailable(
        "WeatherNext is not configured. Set WEATHERNEXT_BQ_TABLE, or "
        "WEATHERNEXT_BQ_PROJECT + WEATHERNEXT_BQ_DATASET after subscribing "
        "to the WeatherNext 2 Analytics Hub dataset."
    )


def default_init_time(target_date: date, lead_day: int = 1, init_hour: int = 0) -> datetime:
    issue_date = target_date - timedelta(days=lead_day)
    return datetime.combine(issue_date, time(hour=init_hour), tzinfo=timezone.utc)


def daily_member_extrema(
    hours: Iterable[WeatherNextHour],
    target_date: date,
    station_tz: str,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return per-member daily TMAX/TMIN for the station-local target date."""
    tz = ZoneInfo(station_tz)
    by_member: dict[str, list[float]] = {}
    for row in hours:
        local_time = row.valid_time.astimezone(tz)
        if local_time.date() != target_date:
            continue
        by_member.setdefault(row.ensemble_member, []).append(row.temp_f)
    tmax = {member: max(vals) for member, vals in by_member.items() if vals}
    tmin = {member: min(vals) for member, vals in by_member.items() if vals}
    return tmax, tmin


def summarize_member_values(values: dict[str, float]) -> dict:
    vals = list(values.values())
    if not vals:
        return {"members": 0, "p10_f": None, "p50_f": None, "p90_f": None, "mean_f": None}
    return {
        "members": len(vals),
        "p10_f": _quantile(vals, 0.10),
        "p50_f": _quantile(vals, 0.50),
        "p90_f": _quantile(vals, 0.90),
        "mean_f": statistics.fmean(vals),
    }


def fetch_hourly_ensemble(
    station: str,
    init_time: datetime,
    *,
    table: str | None = None,
    max_distance_m: int = 75_000,
) -> list[WeatherNextHour]:
    """Query WeatherNext 2 ensemble temperature rows for one station/run."""
    table_name = resolve_table(table)
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise WeatherNextUnavailable(
            "Install google-cloud-bigquery and authenticate with Google Cloud "
            "before querying WeatherNext."
        ) from exc

    s = STATIONS[station]
    query = f"""
    WITH nearest AS (
      SELECT
        t.forecast,
        ST_DISTANCE(ST_CENTROID(t.geography_polygon), ST_GEOGPOINT(@lon, @lat)) AS distance_m
      FROM `{table_name}` AS t
      WHERE t.init_time = @init_time
        AND ST_DWITHIN(t.geography_polygon, ST_GEOGPOINT(@lon, @lat), @max_distance_m)
      ORDER BY distance_m
      LIMIT 1
    )
    SELECT
      f.time AS valid_time,
      CAST(e.ensemble_member AS STRING) AS ensemble_member,
      e.`2m_temperature` AS temp_k
    FROM nearest, UNNEST(forecast) AS f, UNNEST(f.ensemble) AS e
    WHERE e.`2m_temperature` IS NOT NULL
    ORDER BY valid_time, ensemble_member
    """
    client = bigquery.Client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("lat", "FLOAT64", s.lat),
            bigquery.ScalarQueryParameter("lon", "FLOAT64", s.lon),
            bigquery.ScalarQueryParameter("init_time", "TIMESTAMP", init_time),
            bigquery.ScalarQueryParameter("max_distance_m", "INT64", max_distance_m),
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())
    if not rows:
        raise WeatherNextUnavailable(
            f"No WeatherNext rows found for {station} init={init_time.isoformat()}"
        )
    return [
        WeatherNextHour(
            valid_time=row.valid_time.replace(tzinfo=timezone.utc)
            if row.valid_time.tzinfo is None
            else row.valid_time.astimezone(timezone.utc),
            ensemble_member=str(row.ensemble_member),
            temp_f=_k_to_f(row.temp_k),
        )
        for row in rows
    ]


def fetch_forecast_daily(
    station: str,
    target_date: date,
    *,
    init_time: datetime | None = None,
    lead_day: int = 1,
    init_hour: int = 0,
    table: str | None = None,
    fail_soft: bool = True,
) -> dict:
    """Return daily WeatherNext ensemble summary for a station/date."""
    init_time = init_time or default_init_time(target_date, lead_day=lead_day, init_hour=init_hour)
    try:
        hours = fetch_hourly_ensemble(station, init_time, table=table)
        tmax_by_member, tmin_by_member = daily_member_extrema(hours, target_date, STATIONS[station].tz)
        tmax = summarize_member_values(tmax_by_member)
        tmin = summarize_member_values(tmin_by_member)
        return {
            "model": "WeatherNext2",
            "available": bool(tmax["members"]),
            "station": station,
            "target_date": target_date.isoformat(),
            "init_time": init_time.isoformat(),
            "tmax_p10_f": tmax["p10_f"],
            "tmax_p50_f": tmax["p50_f"],
            "tmax_p90_f": tmax["p90_f"],
            "tmax_mean_f": tmax["mean_f"],
            "tmin_p10_f": tmin["p10_f"],
            "tmin_p50_f": tmin["p50_f"],
            "tmin_p90_f": tmin["p90_f"],
            "tmin_mean_f": tmin["mean_f"],
            "members": tmax["members"],
            "error": None if tmax["members"] else "No rows for target local date.",
        }
    except WeatherNextUnavailable as exc:
        if not fail_soft:
            raise
        return {
            "model": "WeatherNext2",
            "available": False,
            "station": station,
            "target_date": target_date.isoformat(),
            "init_time": init_time.isoformat(),
            "tmax_p50_f": None,
            "members": 0,
            "error": str(exc),
        }
