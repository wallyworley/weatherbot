from datetime import datetime, timedelta, timezone

from research.market_reaction_latency import _lag_minutes, reprice_onset

T0 = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)


def _series(points):
    return [(T0 + timedelta(minutes=m), c) for m, c in points]


def test_onset_after_official_ts_positive_lag():
    # baseline 80.0 at -5 and 0; jumps to 80.2 (>=0.10) at +6
    series = _series([(-5, 80.0), (0, 80.0), (3, 80.05), (6, 80.2), (10, 80.2)])
    onset = reprice_onset(series, T0, pre_min=30, post_min=60, threshold=0.10)
    assert onset == T0 + timedelta(minutes=6)
    # first_seen at +1 -> market moved 5 min AFTER we saw it (positive lag)
    assert _lag_minutes(onset, T0 + timedelta(minutes=1)) == 5.0


def test_market_moves_before_first_seen_negative_lag():
    # onset at +2, but we did not see the info until +9 -> negative lag (market led)
    series = _series([(-3, 70.0), (0, 70.0), (2, 70.3), (8, 70.3)])
    onset = reprice_onset(series, T0)
    assert onset == T0 + timedelta(minutes=2)
    assert _lag_minutes(onset, T0 + timedelta(minutes=9)) == -7.0


def test_no_onset_when_market_flat_is_censored():
    series = _series([(-5, 60.0), (0, 60.0), (5, 60.04), (30, 60.05)])
    assert reprice_onset(series, T0, threshold=0.10) is None


def test_no_baseline_returns_none():
    # all points strictly after official_ts -> no baseline
    series = _series([(5, 50.0), (10, 50.5)])
    assert reprice_onset(series, T0) is None


def test_move_must_exceed_threshold():
    series = _series([(0, 90.0), (4, 90.09)])  # 0.09 < 0.10
    assert reprice_onset(series, T0, threshold=0.10) is None
