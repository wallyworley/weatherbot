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
    METAR_GUARD_MAX_DELTA_F,
    METAR_GUARD_WINDOW_MIN,
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


def filter_implausible_swings(rows: list[dict],
                                max_delta_f: float = METAR_GUARD_MAX_DELTA_F,
                                window_min: int = METAR_GUARD_WINDOW_MIN) -> list[dict]:
    """Drop METAR rows that show physically implausible temperature swings vs
    the most recent prior reading (in-batch or in DB).

    Catches stale-data bugs (e.g., NWS station API occasionally returning
    afternoon-warm readings mixed into overnight sequences — confirmed real
    issue in dailydewpoint.com's April 5 release notes) BEFORE they pollute
    daily TMAX, bias correction, or settlement reconciliation.

    Conservative defaults: 10°F in 30 min. Real weather can swing 6-8°F in
    that window during convective passage; we only catch the impossible
    ones. Tune via METAR_GUARD_MAX_DELTA_F / METAR_GUARD_WINDOW_MIN env vars.
    """
    by_station: dict[str, list[dict]] = {}
    for r in rows:
        by_station.setdefault(r["station"], []).append(r)

    kept_total: list[dict] = []
    dropped_total = 0
    for station, station_rows in by_station.items():
        station_rows.sort(key=lambda r: r["obs_time"])
        # Boundary check: most recent prior obs already in DB
        db_prev = None
        try:
            with persistence.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT temp_f, obs_time FROM metar_obs WHERE station=%s "
                    "ORDER BY obs_time DESC LIMIT 1",
                    (station,),
                )
                r = cur.fetchone()
                if r and r["temp_f"] is not None:
                    db_prev = (float(r["temp_f"]), r["obs_time"])
        except Exception as exc:
            log.warning("metar guard: DB lookup for %s failed (%s); skipping boundary check", station, exc)

        kept: list[dict] = []
        for new in station_rows:
            new_temp = new.get("temp_f")
            new_time = new["obs_time"]
            if new_temp is None:
                kept.append(new)
                continue

            # Find most recent prior reading: last kept row, or DB row, whichever is newer.
            prior = None
            if kept:
                last_kept = next((k for k in reversed(kept) if k.get("temp_f") is not None), None)
                if last_kept is not None:
                    prior = (float(last_kept["temp_f"]), last_kept["obs_time"])
            if (prior is None or (db_prev is not None and db_prev[1] > prior[1])) and db_prev is not None:
                if db_prev[1] < new_time:
                    prior = db_prev

            if prior is not None:
                dt_min = (new_time - prior[1]).total_seconds() / 60.0
                if 0 < dt_min < window_min:
                    delta = abs(new_temp - prior[0])
                    if delta > max_delta_f:
                        log.warning(
                            "metar guard: dropped %s @ %s (temp=%.1f°F, prior=%.1f°F at %s, "
                            "Δ=%.1f°F in %.0f min — implausible)",
                            station, new_time, new_temp, prior[0], prior[1], delta, dt_min,
                        )
                        dropped_total += 1
                        continue
            kept.append(new)
        kept_total.extend(kept)

    if dropped_total:
        log.info("metar guard: dropped %d/%d rows as implausible", dropped_total, len(rows))
    return kept_total


def compute_daily(station: Station, metars: Iterable[dict], day: date) -> dict | None:
    """Compute Tmax/Tmin for `day` in the station's local timezone.

    Returns None if:
      - the local day is not yet complete (partial obs would give a wrong
        daily tmax/tmin and pollute bias training / verification), OR
      - the input `metars` do not cover the full local day (early-morning-only
        slivers would mis-report the cold morning low as the daily high).

    2026-05-18: the coverage gate was added after a bug where the live
    `run(hours=36, days_back=2)` path wrote the day-before-yesterday's
    early-morning samples as a "daily tmax", clobbering the correct backfill
    values via the upsert. That contaminated KMIA's bias table by +7F over
    11 consecutive days and drove a bad paper trade on KXHIGHMIA-26MAY18.
    """
    tz = pytz.timezone(station.tz)
    local_start = tz.localize(datetime.combine(day, datetime.min.time()))
    local_end = local_start + timedelta(days=1)
    # Skip days that haven't finished yet (in the station's local tz).
    now_utc = datetime.now(tz=timezone.utc)
    if local_end > now_utc:
        return None
    samples: list[tuple[datetime, float]] = []
    for m in metars:
        t_local = m["obs_time"].astimezone(tz)
        if local_start <= t_local < local_end and m.get("temp_f") is not None:
            samples.append((t_local, m["temp_f"]))
    if not samples:
        return None
    # Coverage gate: refuse to compute when the input doesn't span the full
    # local day. We require the earliest sample at or before 02:00 local
    # (catches the overnight low window) AND the latest at or after 20:00
    # local (catches the late-afternoon peak window). This is conservative;
    # ASOS stations produce 5-min HFMETAR with very high density, so a
    # complete day has both bounds easily. Partial days fall through to
    # backfill or the next live run.
    first_local = min(t for t, _ in samples)
    last_local = max(t for t, _ in samples)
    morning_cutoff = local_start + timedelta(hours=2)
    evening_cutoff = local_start + timedelta(hours=20)
    if first_local > morning_cutoff or last_local < evening_cutoff:
        return None
    temps = [t for _, t in samples]
    return dict(
        station=station.code,
        local_date=day,
        tmax_f=max(temps),
        tmin_f=min(temps),
        source="METAR",
    )


def run(hours: int = 36, days_back: int = 2) -> None:
    """Pull recent METARs (live path). Use `backfill(days=N)` for history.

    For ASOS stations (`station.is_asos`), pulls 5-min HFMETAR via IEM —
    same source as the historical backfill, keeping daily_obs consistently
    HFMETAR-derived for those stations. Non-ASOS stations (KNYC) stay on
    aviationweather.gov hourly METAR. Live HFMETAR latency is ~2-5 minutes.
    """
    from weather_bot.data import iem_fetcher  # local import to avoid cycles

    all_metars: list[dict] = []
    daily_rows: list[dict] = []
    failed: list[tuple[str, str]] = []  # (station_code, error_str)
    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        try:
            if station.is_asos:
                # IEM `hours` is integer; round up to be safe at the lookback edge.
                metars = iem_fetcher.fetch_recent(code, hours=int(hours) + 1)
                source_tag = "HFMETAR"
            else:
                metars = fetch(code, hours)
                source_tag = "METAR"
            # Guard against implausible swings BEFORE compute_daily runs so
            # corrupt readings don't pollute daily TMAX (and downstream bias
            # correction). Filter is per-station to give clean prior-comparison.
            metars = filter_implausible_swings(metars)
            all_metars.extend(metars)
            log.info("METAR: %s source=%s fetched=%d (post-guard)", code, source_tag, len(metars))

            today_local = datetime.now(tz=pytz.timezone(station.tz)).date()
            for offset in range(days_back + 1):
                d = today_local - timedelta(days=offset)
                row = compute_daily(station, metars, d)
                if row:
                    row["source"] = source_tag
                    daily_rows.append(row)
        except Exception as exc:
            log.error("METAR fetch failed for %s: %s", code, exc)
            failed.append((code, str(exc)))
            continue

    if all_metars:
        persistence.upsert_metar(all_metars, record_provenance=True)
    if daily_rows:
        persistence.upsert_daily_obs(daily_rows)
    log.info("Persisted %d METAR rows, %d daily rows (failed stations: %s)",
             len(all_metars), len(daily_rows),
             ", ".join(f"{c}({e})" for c, e in failed) or "none")
    if failed and len(failed) == len(ACTIVE_STATIONS):
        # All stations failed — surface as a job-level failure so launchd /
        # check_morning notice it. Partial failures are tolerated.
        raise RuntimeError(f"METAR fetch failed for all stations: {failed}")


def backfill(days: int) -> None:
    """Pull `days` days of METARs from IEM, compute daily Tmax/Tmin.

    For ASOS stations (`station.is_asos`), uses the 5-min HFMETAR feed which
    eliminates the systematic ~0.8°F undercount of hourly :53 METAR vs CLI
    (verified 2026-05-03 backtest). For non-ASOS sites like KNYC (coop) the
    feed has no sub-hourly rows, so we use the hourly path.

    daily_obs.source is tagged "HFMETAR" or "METAR" accordingly so we can
    audit which method produced each row.
    """
    from weather_bot.data import iem_fetcher  # local import to avoid cycles

    now_utc = datetime.now(tz=timezone.utc)
    end_date = now_utc.date() + timedelta(days=1)    # exclusive upper bound
    start_date = now_utc.date() - timedelta(days=days)

    for code in ACTIVE_STATIONS:
        station = STATIONS[code]
        use_hfmetar = station.is_asos
        source_tag = "HFMETAR" if use_hfmetar else "METAR"
        log.info("METAR backfill (IEM, %s): %s %s -> %s", source_tag, code, start_date, end_date)
        try:
            if use_hfmetar:
                metars = iem_fetcher.fetch_historical_5min(code, start_date, end_date)
            else:
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
                row["source"] = source_tag
                daily_rows.append(row)
        if daily_rows:
            persistence.upsert_daily_obs(daily_rows)
            log.info("METAR %s persisted %d daily Tmax/Tmin rows", code, len(daily_rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
