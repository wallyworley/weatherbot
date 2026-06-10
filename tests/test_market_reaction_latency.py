from datetime import date, datetime, timedelta, timezone

from research.market_reaction_latency import (
    _lag_minutes,
    crossvenue_episodes,
    directional_onset,
    rebin_to_ladder,
    reprice_onset,
    reprice_onset_window,
)

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


# --- Option A windowed onset (model-run channel) ---
def test_window_we_saw_first_positive_lag():
    # baseline 80.0 at -30; flat through first_seen (T0); jumps at +5 -> we saw it first
    series = _series([(-30, 80.0), (-1, 80.0), (5, 80.3), (12, 80.3)])
    onset = reprice_onset_window(series, T0, pre_min=30, post_min=60, threshold=0.10)
    assert onset == T0 + timedelta(minutes=5)
    assert _lag_minutes(onset, T0) == 5.0  # positive: market moved after first_seen


def test_window_already_priced_negative_lag():
    # baseline 70.0 at -30; market already moved at -8 (before first_seen) -> already priced
    series = _series([(-30, 70.0), (-8, 70.4), (3, 70.4)])
    onset = reprice_onset_window(series, T0, pre_min=30, post_min=60, threshold=0.10)
    assert onset == T0 - timedelta(minutes=8)
    assert _lag_minutes(onset, T0) == -8.0  # negative: market priced the run before we saw it


def test_window_no_baseline_before_prewindow():
    series = _series([(-5, 60.0), (5, 60.5)])  # nothing at/before T0-30
    assert reprice_onset_window(series, T0, pre_min=30) is None


# --- Cross-venue locked statistic (codex call 2026-06-09 / f2e7031) ---
VD = date(2026, 6, 10)


def test_directional_onset_requires_direction():
    # Kalshi moves DOWN 0.3F; episode direction is UP (poly warmer) -> no onset.
    series = _series([(-30, 80.0), (-1, 80.0), (5, 79.7), (12, 79.7)])
    assert directional_onset(series, T0, direction=+1.0) is None
    # Same series scored with direction DOWN finds the onset.
    assert directional_onset(series, T0, direction=-1.0) == T0 + timedelta(minutes=5)


def test_directional_onset_before_t0_is_already_priced():
    series = _series([(-30, 70.0), (-7, 70.2), (3, 70.2)])
    onset = directional_onset(series, T0, direction=+1.0)
    assert onset == T0 - timedelta(minutes=7)
    assert _lag_minutes(onset, T0) == -7.0


def _pm(points):
    return [(T0 + timedelta(minutes=m), c) for m, c in points]


def test_episode_freshness_and_rearm():
    # Kalshi flat at 80.0 the whole time.
    kalshi = _series([(-40, 80.0), (0, 80.0), (60, 80.0), (120, 80.0)])
    # PM: in-band at -10 (arms), diverges +0.6 at 0 (fresh), still +0.6 at 5 and 10
    # (continuation, NOT new events), back in band at 15 (re-arms), diverges again at 20.
    pm = _pm([(-10, 80.1), (0, 80.6), (5, 80.6), (10, 80.62), (15, 80.1), (20, 80.7)])
    eps = crossvenue_episodes("KMIA", VD, pm, kalshi)
    starts = [(e.t0 - T0).total_seconds() / 60.0 for e in eps]
    assert starts == [0.0, 20.0]
    # Kalshi never follows -> both are no-follow episodes, none scored.
    assert all(e.kind == "no_follow" for e in eps)


def test_episode_left_censored_without_prior_pm_obs():
    kalshi = _series([(-40, 80.0), (0, 80.0), (60, 80.0)])
    # First PM obs of the day already diverged; no paired obs in the prior 15 min.
    pm = _pm([(0, 80.8), (5, 80.8)])
    eps = crossvenue_episodes("KMIA", VD, pm, kalshi)
    assert len(eps) == 1 and eps[0].kind == "left_censored"


def test_episode_scored_positive_lag_when_kalshi_follows():
    # Kalshi flat 80.0, then moves up 0.15F nine minutes after the PM divergence.
    kalshi = _series([(-40, 80.0), (-5, 80.0), (9, 80.15), (30, 80.15)])
    pm = _pm([(-10, 80.05), (0, 80.6)])
    eps = crossvenue_episodes("KMIA", VD, pm, kalshi)
    assert len(eps) == 1
    e = eps[0]
    assert e.kind == "scored" and e.lag_min == 9.0 and e.gap_reduced is True


def test_episode_sign_flip_rearms_without_visiting_band():
    kalshi = _series([(-40, 80.0), (0, 80.0), (60, 80.0)])
    # +0.6 at 0 (fresh), -0.6 at 5 (sign flip -> fresh even though |gap| never < 0.25).
    pm = _pm([(-10, 80.0), (0, 80.6), (5, 79.4)])
    eps = crossvenue_episodes("KMIA", VD, pm, kalshi)
    assert [(e.gap0 > 0) for e in eps] == [True, False]


def test_rebin_support_fraction_and_normalization():
    # Source: two 2F buckets fully inside dst, one fully outside.
    src = [(80.0, 82.0, 0.5), (82.0, 84.0, 0.3), (90.0, 92.0, 0.2)]
    dst = [(80.0, 82.0), (82.0, 84.0)]
    probs, support = rebin_to_ladder(src, dst, typical_width=2.0)
    assert abs(support - 0.8) < 1e-9
    assert abs(sum(probs) - 1.0) < 1e-9
    assert abs(probs[0] - 0.5 / 0.8) < 1e-9


def test_rebin_splits_offset_buckets_by_overlap():
    # Source bucket [81, 83) overlaps dst [80,82) and [82,84) by half each.
    src = [(81.0, 83.0, 1.0)]
    dst = [(80.0, 82.0), (82.0, 84.0)]
    probs, support = rebin_to_ladder(src, dst, typical_width=2.0)
    assert abs(support - 1.0) < 1e-9
    assert abs(probs[0] - 0.5) < 1e-9 and abs(probs[1] - 0.5) < 1e-9
