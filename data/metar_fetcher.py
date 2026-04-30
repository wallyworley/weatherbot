"""
METAR observation collector.

Pulls hourly METARs from aviationweather.gov, persists raw hourly obs, and
computes a daily Tmax/Tmin per station (local calendar day).

NOTE on settlement: Kalshi daily temp contracts settle on the NWS-reported
daily high/low, which for KNYC is the Central Park cooperative observation
and for airport stations is the ASOS 6-hour max/min. For MVP we use METAR
T/Td aggregation, which is a close but not exact proxy for the NWS daily
value at KORD/KLAX/etc. Improvement path: integrate NWS xmACIS2 for
ground-truth settlement data.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import pytz
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from weather_bot.config import (
    ACTIVE_STATIONS,
    AVIATION_WEATHER_METAR_URL,
    AVIATION_WEATHER_METAR_URL_DATED,
    METAR_BACKFILL_CHUNK_HOURS,
    STATIONS,
    Station,
)
from weather_bot.data import persistence

log = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def _fetch_metars(station: str, hours: int, end_utc: datetime | None = None) -> list[dict]:
    """Fetch METARs ending at `end_utc` (or now), looking back `hours`.

    aviationweather.gov reliably returns ~72h per call — chunk larger windows.
    """
    if end_utc is None:
        url = AVIATION_WEATHER_METAR_URL.format(station=station, hours=hours)
    else:
        url = AVIATION_WEATHER_METAR_URL_DATED.format(
            station=station,
            hours=hours,
            date=end_utc.strftime("%Y%m%d_%H%M") + "Z",
        )
    resp = requests.get(url, timeout=60, headers={"User-Agent": "weather-bot/0.1"})
    resp.raise_for_status()
    return resp.json() or []


def _c_to_f(x: float | None) -> float | None:
    return None if x is None else x * 9.0 / 5.0 + 32.0


def _parse_metar_rows(raw: list[dict], station: str) -> list[dict]:
    rows: list[dict] = []
    for m in raw:
        try:
            obs_time = datetime.fromisoformat(m["reportTime"].replace("Z", "+00:00"))
        except Exception:
            ts = m.get("obsTime")
            if ts is None:
                continue
            obs_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        rows.append(
            dict(
                station=station,
                obs_time=obs_time,
                temp_f=_c_to_f(m.get("temp")),
                dewpoint_f=_c_to_f(m.get("dewp")),
                wind_kt=m.get("wspd"),
                raw=m.get("rawOb"),
            )
        )
    return rows


def fetch(station: str, hours: int = 36) -> list[dict]:
    """Return hourly METAR rows ending now, looking back `hours`."""
    return _parse_metar_rows(_fetch_metars(station, hours), station)


def fetch_range(station: str, hours_back: int, end_utc: datetime | None = None) -> list[dict]:
    """Fetch up to `hours_back` hours of METARs ending at `end_utc`.

    Chunks into METAR_BACKFILL_CHUNK_HOURS windows because the API rejects /
    truncates larger requests.
    """
    if end_utc is None:
        end_utc = datetime.now(tz=timezone.utc)
    out: dict[datetime, dict] = {}
    remaining = hours_back
    cursor_end = end_utc
    while remaining > 0:
        chunk = min(METAR_BACKFILL_CHUNK_HOURS, remaining)
        try:
            raw = _fetch_metars(station, chunk, end_utc=cursor_end)
        except Exception as exc:
            log.warning("METAR fetch failed at %s: %s", cursor_end, exc)
            raw = []
        for row in _parse_metar_rows(raw, station):
            out[row["obs_time"]] = row
        log.info("METAR %s ending=%s chunk=%dh got=%d", station, cursor_end, chunk, len(raw))
        cursor_end = cursor_end - timedelta(hours=chunk)
        remaining -= chunk
    return list(out.values())


def compute_daily(station: Station, metars: Iterable[dict], day: date) -> dict | None:
    """Compute Tmax/Tmin for `day` in the station's local timezone.

    Returns None if the local day is not yet complete — partial obs would
    give a wrong daily tmax/tmin and pollute bias training / verification.
    """
    tz = pytz.timezone(station.tz)
    local_start = tz.localize(datetime.combine(day, datetime.min.time()))
    local_end = local_start + timedelta(days=1)
    # Skip days that haven't finished yet (in the station's local tz).
    now_utc = datetime.now(tz=timezone.utc)
    if local_end > now_utc:
        return None
    samples: list[float] = []
    for m in metars:
        t_local = m["obs_time"].astimezone(tz)
        if local_start <= t_local < local_end and m.get("temp_f") is not None:
            samples.append(m["temp_f"])
    if not samples:
        return None
    return dict(
        station=station.code,
        local_date=day,
        tmax_f=max(samples),
        tmin_f=min(samples),
        source="METAR",
    )


def run(hours: int = 36, days_back: int = 2) -> None:
    """Pull recent METARs (live path). Use `backfill(days=N)` for history."""
    all_metars: list[dict] = []
    daily_rows: list[dict] = []
    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        metars = fetch(code, hours)
        all_metars.extend(metars)
        log.info("METAR: %s fetched=%d", code, len(metars))

        today_local = datetime.now(tz=pytz.timezone(station.tz)).date()
        for offset in range(days_back + 1):
            d = today_local - timedelta(days=offset)
            row = compute_daily(station, metars, d)
            if row:
                daily_rows.append(row)

    if all_metars:
        persistence.upsert_metar(all_metars)
    if daily_rows:
        persistence.upsert_daily_obs(daily_rows)
    log.info("Persisted %d METAR rows, %d daily rows", len(all_metars), len(daily_rows))


def backfill(days: int) -> None:
    """Pull `days` days of METARs from IEM ASOS archive, compute daily Tmax/Tmin.

    Uses Iowa Environmental Mesonet as the historical source — aviationweather.gov
    is unreliable beyond ~48h. Single CSV request per station, no chunking.
    """
    from weather_bot.data import iem_fetcher  # local import to avoid cycles

    now_utc = datetime.now(tz=timezone.utc)
    end_date = now_utc.date() + timedelta(days=1)    # exclusive upper bound
    start_date = now_utc.date() - timedelta(days=days)

    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        log.info("METAR backfill (IEM): %s %s -> %s", code, start_date, end_date)
        try:
            metars = iem_fetcher.fetch_historical(code, start_date, end_date)
        except Exception as exc:
            log.error("IEM backfill failed for %s: %s", code, exc)
            continue

        if metars:
            persistence.upsert_metar(metars)
            log.info("METAR %s persisted %d raw obs", code, len(metars))

        # Compute daily Tmax/Tmin for each calendar day in window.
        today_local = now_utc.astimezone(pytz.timezone(station.tz)).date()
        daily_rows: list[dict] = []
        for offset in range(days + 1):
            d = today_local - timedelta(days=offset)
            row = compute_daily(station, metars, d)
            if row:
                daily_rows.append(row)
        if daily_rows:
            persistence.upsert_daily_obs(daily_rows)
            log.info("METAR %s persisted %d daily Tmax/Tmin rows", code, len(daily_rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
