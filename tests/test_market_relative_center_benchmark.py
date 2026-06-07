"""Frozen regression tests for the market-relative center benchmark.

These pin the scoring math AND the canonical coherent-snapshot selection so any
future change to the benchmark is caught. All values below are hand-computed for
the fixture event and must not drift. None of these tests touch the database.
"""
from datetime import date, datetime, timezone

import pytest

from weather_bot.research.market_relative_center_benchmark import (
    BucketRow,
    _crps_discrete,
    _normalize,
    coherent_snapshot_rows,
    score_event,
    score_events,
    summarize,
)


def _row(lower, upper, truth, yes_win, model_p, market_p, *,
         ticker=None, station="KNYC", valid_date=date(2026, 6, 1),
         ts=datetime(2026, 6, 1, 12, tzinfo=timezone.utc), lead_day=0):
    return BucketRow(
        station=station,
        valid_date=valid_date,
        var="TMAX_DAILY",
        lead_day=lead_day,
        ticker=ticker or f"KX-{lower}-{upper}",
        ts=ts,
        lower_f=lower,
        upper_f=upper,
        truth_f=truth,
        yes_win=yes_win,
        model_p=model_p,
        market_p=market_p,
    )


# ---------------------------------------------------------------------------
# Unit behavior
# ---------------------------------------------------------------------------
def test_normalize_clamps_and_sums_to_one():
    out = _normalize([-1.0, 2.0, 0.5])

    assert out == [0.0, 2.0 / 3.0, 1.0 / 3.0]
    assert round(sum(out), 6) == 1.0


def test_crps_discrete_rewards_closer_center():
    close = _crps_discrete([69.0, 71.0, 73.0], [0.1, 0.8, 0.1], 71.0)
    far = _crps_discrete([69.0, 71.0, 73.0], [0.8, 0.1, 0.1], 71.0)

    assert close < far


def test_score_event_compares_same_bucket_set():
    rows = [
        _row(68.0, 70.0, 71.0, 0, 0.20, 0.70),
        _row(70.0, 72.0, 71.0, 1, 0.60, 0.20),
        _row(72.0, 74.0, 71.0, 0, 0.20, 0.10),
    ]

    score = score_event(rows)

    assert score is not None
    assert score.diff_brier < 0
    assert score.diff_rps < 0
    assert score.diff_crps < 0
    assert score.diff_center_abs_error_f < 0


# ---------------------------------------------------------------------------
# FROZEN scoring fixture. Buckets: [68,70) [70,72)*winner [72,74), truth 71.
# model normalized = [0.2,0.6,0.2], market normalized = [0.1,0.8,0.1].
# Hand-computed:
#   Brier(mean/bucket): model 0.08, market 0.02
#   RPS(norm, /2):      model 0.04, market 0.01
#   CRPS(energy,°F):    model 0.16, market 0.04
#   center E[X]:        model 71.0, market 71.0 (both abs err 0)
# ---------------------------------------------------------------------------
_FROZEN = pytest.approx

def _fixture_event(valid_date=date(2026, 6, 1), model=(0.2, 0.6, 0.2)):
    return [
        _row(68.0, 70.0, 71.0, 0, model[0], 0.10, valid_date=valid_date),
        _row(70.0, 72.0, 71.0, 1, model[1], 0.80, valid_date=valid_date),
        _row(72.0, 74.0, 71.0, 0, model[2], 0.10, valid_date=valid_date),
    ]


def test_frozen_event_scores_exact():
    s = score_event(_fixture_event())
    assert s is not None
    assert s.n_buckets == 3
    assert s.model_brier == _FROZEN(0.08, abs=1e-9)
    assert s.market_brier == _FROZEN(0.02, abs=1e-9)
    assert s.diff_brier == _FROZEN(0.06, abs=1e-9)
    assert s.model_rps == _FROZEN(0.04, abs=1e-9)
    assert s.market_rps == _FROZEN(0.01, abs=1e-9)
    assert s.diff_rps == _FROZEN(0.03, abs=1e-9)
    assert s.model_crps == _FROZEN(0.16, abs=1e-9)
    assert s.market_crps == _FROZEN(0.04, abs=1e-9)
    assert s.diff_crps == _FROZEN(0.12, abs=1e-9)
    assert s.model_center_f == _FROZEN(71.0, abs=1e-9)
    assert s.market_center_f == _FROZEN(71.0, abs=1e-9)
    assert s.model_center_abs_error_f == _FROZEN(0.0, abs=1e-9)
    assert s.diff_center_abs_error_f == _FROZEN(0.0, abs=1e-9)


def test_normalization_repairs_unnormalized_model():
    """Un-normalized model probs (sum 2.0) must score identically after repair.

    This pins the audit finding: the benchmark renormalizes the model's
    non-distribution output before scoring (generous to the model). Values are
    kept <= 1.0 each so per-bucket clamping does not alter the shape; the raw
    sum (1.5) is proportional to event A's [0.2,0.6,0.2].
    """
    s = score_event(_fixture_event(model=(0.3, 0.9, 0.3)))  # sums to 1.5
    assert s is not None
    assert s.model_prob_sum == _FROZEN(1.5, abs=1e-9)  # raw sum preserved in record
    assert s.model_brier == _FROZEN(0.08, abs=1e-9)    # but scored after normalize
    assert s.diff_brier == _FROZEN(0.06, abs=1e-9)
    assert s.model_crps == _FROZEN(0.16, abs=1e-9)


def test_summarize_aggregates_paired_deltas():
    scores = score_events(
        _fixture_event(date(2026, 6, 1)) + _fixture_event(date(2026, 6, 2))
    )
    summaries = summarize(scores)
    assert len(summaries) == 1
    g = summaries[0]
    assert g.station == "KNYC" and g.lead_day == 0
    assert g.n_events == 2
    assert g.diff_brier == _FROZEN(0.06, abs=1e-9)
    assert g.diff_rps == _FROZEN(0.03, abs=1e-9)
    assert g.diff_crps == _FROZEN(0.12, abs=1e-9)
    # Paired 95% CI collapses (identical deltas) to the point estimate.
    assert g.diff_crps_ci_low == _FROZEN(0.12, abs=1e-9)
    assert g.diff_crps_ci_high == _FROZEN(0.12, abs=1e-9)


# ---------------------------------------------------------------------------
# FROZEN coherent-snapshot SELECTION (the canonical fix).
# One event, three tick windows: 10:00 has A,B,C (A quoted twice); 10:20 has
# A,B; 10:40 has A. The selection must pick the LATEST window with >= min_buckets
# live buckets and keep the latest signal per ticker within it.
# ---------------------------------------------------------------------------
def _t(minute):
    return datetime(2026, 6, 1, 10, minute, tzinfo=timezone.utc)


def _snapshot_fixture():
    return [
        _row(68.0, 70.0, 71.0, 0, 0.10, 0.10, ticker="A", ts=_t(0)),   # window 10:00
        _row(68.0, 70.0, 71.0, 0, 0.50, 0.10, ticker="A", ts=_t(5)),   # window 10:00 (later -> wins dedup)
        _row(70.0, 72.0, 71.0, 1, 0.60, 0.80, ticker="B", ts=_t(0)),
        _row(72.0, 74.0, 71.0, 0, 0.20, 0.10, ticker="C", ts=_t(0)),
        _row(68.0, 70.0, 71.0, 0, 0.30, 0.10, ticker="A", ts=_t(20)),  # window 10:20
        _row(70.0, 72.0, 71.0, 1, 0.40, 0.80, ticker="B", ts=_t(20)),
        _row(68.0, 70.0, 71.0, 0, 0.30, 0.10, ticker="A", ts=_t(40)),  # window 10:40
    ]


def test_coherent_snapshot_selects_latest_full_window_and_dedups():
    rows, diag = coherent_snapshot_rows(_snapshot_fixture(), tick_minutes=10, min_buckets=3)
    assert diag["events_total"] == 1
    assert diag["events_with_snapshot"] == 1
    assert len(rows) == 3
    assert {r.ticker for r in rows} == {"A", "B", "C"}
    a = next(r for r in rows if r.ticker == "A")
    assert a.model_p == 0.50  # latest signal within the chosen 10:00 window
    assert a.ts == _t(5)


def test_coherent_snapshot_respects_min_buckets():
    rows, diag = coherent_snapshot_rows(_snapshot_fixture(), tick_minutes=10, min_buckets=2)
    # Latest window with >= 2 live buckets is 10:20 (A,B), not 10:00.
    assert diag["events_with_snapshot"] == 1
    assert {r.ticker for r in rows} == {"A", "B"}
    a = next(r for r in rows if r.ticker == "A")
    assert a.ts == _t(20)


def test_coherent_snapshot_drops_events_without_enough_live_buckets():
    rows, diag = coherent_snapshot_rows(_snapshot_fixture(), tick_minutes=10, min_buckets=4)
    assert diag["events_total"] == 1
    assert diag["events_with_snapshot"] == 0
    assert rows == []
