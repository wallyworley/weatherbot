"""Coverage-gate tests for metar_fetcher.compute_daily.

These exist because a missing gate let the live cron clobber correct backfill
daily_obs values with early-morning slivers (2026-05-17 KMIA bias incident).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytz

from weather_bot.config import STATIONS
from weather_bot.data.metar_fetcher import compute_daily


def _metars(times_local, temps):
    tz = pytz.timezone("America/New_York")
    return [
        {"obs_time": tz.localize(t).astimezone(timezone.utc), "temp_f": v}
        for t, v in zip(times_local, temps)
    ]


def test_full_day_coverage_returns_max_min():
    station = STATIONS["KMIA"]
    day = date(2026, 5, 12)
    # Synthetic: every 30 min from 01:00 to 23:30 local with realistic Miami curve.
    times = [datetime.combine(day, datetime.min.time()) + timedelta(minutes=30 * i)
             for i in range(2, 47)]
    temps = [75 + 16 * (1 - abs((i - 28) / 28)) for i in range(2, 47)]
    row = compute_daily(station, _metars(times, temps), day)
    assert row is not None
    assert row["tmax_f"] == max(temps)
    assert row["tmin_f"] == min(temps)


def test_early_morning_only_returns_none():
    """The bug: live cron with hours=36 covers only ~05:00 UTC of day-2,
    which is the overnight low window. compute_daily must refuse."""
    station = STATIONS["KMIA"]
    day = date(2026, 5, 12)
    # Two samples only: 00:30 and 01:30 local — the overnight low window.
    times = [datetime.combine(day, datetime.min.time()) + timedelta(minutes=30),
             datetime.combine(day, datetime.min.time()) + timedelta(minutes=90)]
    temps = [77.0, 77.0]
    row = compute_daily(station, _metars(times, temps), day)
    assert row is None, "early-morning-only sliver must not write a degenerate daily row"


def test_afternoon_only_also_returns_none():
    station = STATIONS["KMIA"]
    day = date(2026, 5, 12)
    # Only afternoon samples — no morning bound.
    times = [datetime.combine(day, datetime.min.time()) + timedelta(hours=h)
             for h in (14, 15, 16, 17)]
    temps = [88.0, 90.0, 91.0, 90.0]
    row = compute_daily(station, _metars(times, temps), day)
    assert row is None


def test_future_day_returns_none():
    station = STATIONS["KMIA"]
    tomorrow = (datetime.now(tz=timezone.utc).astimezone(pytz.timezone(station.tz)).date()
                + timedelta(days=2))
    row = compute_daily(station, [], tomorrow)
    assert row is None
