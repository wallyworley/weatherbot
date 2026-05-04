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
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from weather_bot.config import IEM_ASOS_HFMETAR_URL, IEM_ASOS_RECENT_URL, IEM_ASOS_URL

# Matches the Txxxxxxxx group in METAR remarks, which encodes temp + dewpoint
# to 0.1°C: T<sign><TTT><sign><DDD>. Sign digit: 0=positive, 1=negative.
# This is present on routine METARs and on the 5-min MADIS HFMETAR rows where
# the IEM CSV's tmpf/dwpf columns are null.
_T_GROUP = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")

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


# IEM enforces 1 req/sec/IP on cgi-bin/request/asos.py. We gate every fetch
# through this lock so back-to-back station calls (live run, backtests) can't
# trip the throttle. Slightly above 1.0s for safety.
_IEM_MIN_INTERVAL_SEC = 1.1
_iem_lock = threading.Lock()
_iem_last_fetch_ts: float = 0.0


def _iem_throttle() -> None:
    global _iem_last_fetch_ts
    with _iem_lock:
        elapsed = time.monotonic() - _iem_last_fetch_ts
        if elapsed < _IEM_MIN_INTERVAL_SEC:
            time.sleep(_IEM_MIN_INTERVAL_SEC - elapsed)
        _iem_last_fetch_ts = time.monotonic()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30))
def _fetch_csv(url: str) -> str:
    _iem_throttle()
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


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _parse_t_group(raw: str | None) -> tuple[float | None, float | None]:
    """Extract (temp_f, dewpoint_f) from the Txxxxxxxx METAR remark group.

    The 5-min HFMETAR rows have null tmpf/dwpf columns in IEM CSV but carry
    high-precision values in the raw METAR remarks (0.1°C resolution).
    """
    if not raw:
        return None, None
    m = _T_GROUP.search(raw)
    if not m:
        return None, None
    t_sign, t_val, d_sign, d_val = m.groups()
    t_c = int(t_val) / 10.0 * (-1 if t_sign == "1" else 1)
    d_c = int(d_val) / 10.0 * (-1 if d_sign == "1" else 1)
    return _c_to_f(t_c), _c_to_f(d_c)


def fetch_historical_5min(station: str, start: date, end: date) -> list[dict]:
    """Fetch all obs (routine + SPECI + 5-min HFMETAR) for `station` from
    `start` (inclusive) to `end` (exclusive). For backtests where you need
    historical 5-min cadence, not just hourly METAR.
    """
    iem_id = _strip_k(station)
    url = IEM_ASOS_HFMETAR_URL.format(
        station=iem_id,
        y1=start.year, m1=start.month, d1=start.day,
        y2=end.year, m2=end.month, d2=end.day,
    )
    log.info("IEM hfmetar fetch %s %s -> %s", station, start, end)
    text = _fetch_csv(url)
    return _parse_recent_csv(text, station)


def _parse_recent_csv(text: str, station: str) -> list[dict]:
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
        raw = r.get("metar")
        temp_f = _parse_float(r.get("tmpf"))
        dewpoint_f = _parse_float(r.get("dwpf"))
        if temp_f is None or dewpoint_f is None:
            t_remark, d_remark = _parse_t_group(raw)
            if temp_f is None:
                temp_f = t_remark
            if dewpoint_f is None:
                dewpoint_f = d_remark
        rows.append(
            dict(
                station=station,
                obs_time=obs_time,
                temp_f=temp_f,
                dewpoint_f=dewpoint_f,
                wind_kt=_parse_float(r.get("sknt")),
                raw=raw,
            )
        )
    return rows


def fetch_recent(station: str, hours: int = 2) -> list[dict]:
    """Fetch all recent obs (routine + SPECI + 5-min HFMETAR) for `station`.

    Unlike `fetch_historical`, this hits IEM without the report_type filter
    so it returns ~12 rows/hour (5-min cadence) instead of just the top-of-hour
    METAR. For HFMETAR rows whose CSV tmpf/dwpf are null, temp/dewpoint are
    parsed from the raw METAR's Txxxxxxxx group at 0.1°C precision.

    IEM enforces a 1-second-per-IP throttle; sequence stations with care.

    Returns rows in the same shape as metar_fetcher.fetch().
    """
    iem_id = _strip_k(station)
    url = IEM_ASOS_RECENT_URL.format(station=iem_id, hours=hours)
    log.info("IEM recent fetch %s hours=%d", station, hours)
    text = _fetch_csv(url)
    rows = _parse_recent_csv(text, station)
    log.info("IEM recent %s rows=%d", station, len(rows))
    return rows
