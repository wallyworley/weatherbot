"""Unit tests for the piecewise-CDF distribution builder."""
from datetime import date, datetime, timezone

import numpy as np

from weather_bot.models.distribution import (
    PiecewiseCDF,
    build_cdf_from_percentiles,
    _configured_morning_center_blend_weights,
    lead_day_for_station,
    lead_day_variance_multiplier,
    max_widen_factor_for_lead,
)


def test_cdf_monotonic_and_bounded():
    rows = [
        {"percentile": 10, "value": 60.0},
        {"percentile": 25, "value": 65.0},
        {"percentile": 50, "value": 70.0},
        {"percentile": 75, "value": 75.0},
        {"percentile": 90, "value": 80.0},
    ]
    cdf = build_cdf_from_percentiles(rows)
    xs = np.linspace(40, 100, 61)
    vals = [cdf.cdf(x) for x in xs]
    # monotonic non-decreasing
    for a, b in zip(vals, vals[1:]):
        assert b + 1e-9 >= a
    assert 0.0 < vals[0] < 1.0
    assert 0.0 < vals[-1] < 1.0


def test_bucket_probability_sums_across_partition():
    rows = [
        {"percentile": 10, "value": 60.0},
        {"percentile": 50, "value": 70.0},
        {"percentile": 90, "value": 80.0},
    ]
    cdf = build_cdf_from_percentiles(rows)
    p_low = cdf.prob_between(None, 65)
    p_mid = cdf.prob_between(65, 75)
    p_high = cdf.prob_between(75, None)
    total = p_low + p_mid + p_high
    assert abs(total - 1.0) < 1e-6


def test_median_near_50th_percentile():
    rows = [
        {"percentile": 25, "value": 65.0},
        {"percentile": 50, "value": 70.0},
        {"percentile": 75, "value": 75.0},
    ]
    cdf = build_cdf_from_percentiles(rows)
    assert abs(cdf.median() - 70.0) < 0.1


def test_shift_moves_distribution():
    rows = [
        {"percentile": 50, "value": 70.0},
        {"percentile": 90, "value": 80.0},
    ]
    cdf = build_cdf_from_percentiles(rows)
    p_before = cdf.prob_between(72, 78)
    cdf.shift = 5.0
    p_after = cdf.prob_between(77, 83)
    # Probability of a window that moves with the shift should stay ~same.
    assert abs(p_before - p_after) < 0.05


def test_lead_day_uses_station_local_date():
    # 2026-05-07 03:00 UTC is still May 6 in Chicago.
    now_utc = datetime(2026, 5, 7, 3, 0, tzinfo=timezone.utc)
    assert lead_day_for_station("KMDW", date(2026, 5, 7), now_utc) == 1
    assert lead_day_for_station("KNYC", date(2026, 5, 7), now_utc) == 1

    # But after midnight Eastern, KNYC should be same-day while KMDW is still L1.
    now_utc = datetime(2026, 5, 7, 4, 30, tzinfo=timezone.utc)
    assert lead_day_for_station("KNYC", date(2026, 5, 7), now_utc) == 0
    assert lead_day_for_station("KMDW", date(2026, 5, 7), now_utc) == 1


def test_lead_day_variance_schedule_is_lead_aware():
    assert lead_day_variance_multiplier(0) == 1.0
    assert lead_day_variance_multiplier(1) == 1.25
    assert lead_day_variance_multiplier(2) == 1.15
    assert lead_day_variance_multiplier(3) == 1.05
    # lead=0 widening was bumped 1.10 → 1.40 then reverted to 1.10 on
    # 2026-05-29 (Opus 4.8 review): the overconfidence is flat/location-shaped
    # (~+17pp above the 20% bucket, low buckets already calibrated), which the
    # per-bin calibrator handles correctly — widening the distribution would
    # corrupt the already-calibrated low buckets. See distribution.py docstring.
    assert max_widen_factor_for_lead(0) == 1.10
    assert max_widen_factor_for_lead(1) == 1.35


def test_morning_center_policy_defaults_to_current(monkeypatch):
    from weather_bot import config

    monkeypatch.setattr(config, "MORNING_CENTER_POLICY", "current")

    assert _configured_morning_center_blend_weights("KAUS", "TMAX_DAILY", 0, 7) is None


def test_morning_center_policy_station_gfs_is_morning_tmax_only(monkeypatch):
    from weather_bot import config

    monkeypatch.setattr(config, "MORNING_CENTER_POLICY", "station_gfs_50_50")
    monkeypatch.setattr(config, "MORNING_CENTER_GFS_STATIONS", ["KAUS"])
    monkeypatch.setattr(config, "MORNING_CENTER_START_HOUR", 6)
    monkeypatch.setattr(config, "MORNING_CENTER_END_HOUR", 9)

    assert _configured_morning_center_blend_weights("KAUS", "TMAX_DAILY", 0, 7) == {
        "NBM": 0.5,
        "GFS": 0.5,
    }
    assert _configured_morning_center_blend_weights("KNYC", "TMAX_DAILY", 0, 7) == {
        "NBM": 1.0,
    }
    assert _configured_morning_center_blend_weights("KAUS", "TMAX_DAILY", 0, 10) is None
    assert _configured_morning_center_blend_weights("KAUS", "TMIN_DAILY", 0, 7) is None
    assert _configured_morning_center_blend_weights("KAUS", "TMAX_DAILY", 1, 7) is None
