"""
Iowa Environmental Mesonet (IEM) ASOS historical METAR fetcher.

IEM maintains a canonical archive of all ASOS/AWOS observations going back
decades. Use this for backfill; aviationweather.gov is unreliable for
anything beyond the last ~48 hours.

IEM returns CSV in a single request — no chunking needed.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta, timezone

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from weather_bot.config import IEM_ASOS_URL

log = logging.getLogger(__name__)


def _strip_k(station: str) -> str:
    """IEM uses 3-letter station IDs (NYC, ORD) — drop leading 'K'."""
    return station[1:] if len(station) == 4 and station.startswith("K") else station


def _parse_float(x: str | None) -> float | None:
    if x is None or x == "" or x in ("M", "null", "T"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30))
def _fetch_csv(url: str) -> str:
    resp = requests.get(url, timeout=120, headers={"User-Agent": "weather-bot/0.1"})
    resp.raise_for_status()
    return resp.text


def fetch_historical(station: str, start: date, end: date) -> list[dict]:
    """Fetch all METARs for `station` from `start` (inclusive) to `end` (exclusive).

    Returns rows in the same shape as metar_fetcher.fetch():
      {station, obs_time (UTC), temp_f, dewpoint_f, wind_kt, raw}
    """
    iem_id = _strip_k(station)
    url = IEM_ASOS_URL.format(
        station=iem_id,
        y1=start.year, m1=start.month, d1=start.day,
        y2=end.year, m2=end.month, d2=end.day,
    )
    log.info("IEM fetch %s %s -> %s", station, start, end)
    text = _fetch_csv(url)
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict] = []
    for r in reader:
        valid = r.get("valid")
        if not valid:
            continue
        try:
            obs_time = datetime.strptime(valid, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        rows.append(
            dict(
                station=station,
                obs_time=obs_time,
                temp_f=_parse_float(r.get("tmpf")),
                dewpoint_f=_parse_float(r.get("dwpf")),
                wind_kt=_parse_float(r.get("sknt")),
                raw=r.get("metar"),
            )
        )
    log.info("IEM %s rows=%d", station, len(rows))
    return rows
