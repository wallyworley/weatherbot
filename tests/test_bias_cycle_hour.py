"""Tests for cycle-hour-aware bias lookups.

Hits the live local DB; skipped if not reachable.
"""
from __future__ import annotations

import pytest

from weather_bot.data import persistence


def _has_db():
    try:
        with persistence.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_db(), reason="local DB not reachable")


def _seed(rows):
    persistence.upsert_station_bias(rows)


def _cleanup(station):
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM station_bias WHERE station=%s", (station,))
        conn.commit()


def test_cycle_lookup_prefers_cycle_specific_when_thick():
    s = "KNYC"
    _cleanup(s)
    try:
        _seed([
            dict(station=s, model="UNITTEST", var="TMAX_DAILY", month=5, lead_day=0,
                 cycle_hour=-1, mean_bias_f=1.0, stddev_f=2.0, sample_size=30),
            dict(station=s, model="UNITTEST", var="TMAX_DAILY", month=5, lead_day=0,
                 cycle_hour=12, mean_bias_f=3.5, stddev_f=4.0, sample_size=20),
        ])
        row = persistence.get_station_bias(s, "UNITTEST", "TMAX_DAILY", 5, 0, cycle_hour=12)
        assert row is not None
        assert row["cycle_hour"] == 12
        assert row["mean_bias_f"] == 3.5
    finally:
        _cleanup(s)


def test_cycle_lookup_falls_back_when_thin():
    s = "KNYC"
    _cleanup(s)
    try:
        _seed([
            dict(station=s, model="UNITTEST", var="TMAX_DAILY", month=5, lead_day=0,
                 cycle_hour=-1, mean_bias_f=1.0, stddev_f=2.0, sample_size=30),
            dict(station=s, model="UNITTEST", var="TMAX_DAILY", month=5, lead_day=0,
                 cycle_hour=12, mean_bias_f=3.5, stddev_f=4.0, sample_size=3),  # thin
        ])
        row = persistence.get_station_bias(s, "UNITTEST", "TMAX_DAILY", 5, 0, cycle_hour=12)
        assert row is not None
        assert row["cycle_hour"] == -1
        assert row["mean_bias_f"] == 1.0
    finally:
        _cleanup(s)


def test_legacy_callers_get_cycle_agnostic_row():
    s = "KNYC"
    _cleanup(s)
    try:
        _seed([
            dict(station=s, model="UNITTEST", var="TMAX_DAILY", month=5, lead_day=0,
                 cycle_hour=-1, mean_bias_f=1.0, stddev_f=2.0, sample_size=30),
            dict(station=s, model="UNITTEST", var="TMAX_DAILY", month=5, lead_day=0,
                 cycle_hour=12, mean_bias_f=3.5, stddev_f=4.0, sample_size=30),
        ])
        # No cycle_hour passed → must return -1 lane, never the cycle-specific row.
        row = persistence.get_station_bias(s, "UNITTEST", "TMAX_DAILY", 5, 0)
        assert row is not None
        assert row["cycle_hour"] == -1
    finally:
        _cleanup(s)
