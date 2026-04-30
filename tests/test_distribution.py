"""Unit tests for the piecewise-CDF distribution builder."""
import numpy as np

from weather_bot.models.distribution import PiecewiseCDF, build_cdf_from_percentiles


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
