from datetime import date, datetime, timezone

import pytest

from weather_bot.research.market_information_forensics import (
    BucketTick,
    _boundary_distance,
    _center_from_prob_map,
    _classify_market_vs_metar,
    _normalize,
    coherent_snapshot_groups,
)


def _tick(ticker, minute, lower, upper, *, market_p=0.2, weatherbot_p=0.2):
    return BucketTick(
        station="KNYC",
        valid_date=date(2026, 6, 1),
        var="TMAX_DAILY",
        event_ticker="EVT",
        ticker=ticker,
        snapshot_ts=datetime(2026, 6, 1, 12, minute, tzinfo=timezone.utc),
        lead_day=0,
        lower_f=lower,
        upper_f=upper,
        market_p=market_p,
        weatherbot_p=weatherbot_p,
        signal_ts=None,
        live_metar_max_f=71.8,
        latest_metar_ts=None,
        cli_tmax_f=None,
        cli_issued_at=None,
        nbm_percentiles=None,
        det_centers=None,
        guidance_values=None,
        past_market_p={1: None, 5: None, 10: None, 30: None, 60: None},
        future_market_p={1: None, 5: None, 10: None, 30: None, 60: None},
    )


def test_normalize_clamps_and_rejects_missing():
    assert _normalize([0.2, 0.3, 0.5]) == pytest.approx([0.2, 0.3, 0.5])
    assert _normalize([-1.0, 2.0, 0.5]) == pytest.approx([0.0, 2.0 / 3.0, 1.0 / 3.0])
    assert _normalize([0.2, None, 0.8]) is None


def test_coherent_snapshot_groups_every_valid_window_and_dedups():
    ticks = [
        _tick("A", 0, 68.0, 70.0, market_p=0.1),
        _tick("A", 5, 68.0, 70.0, market_p=0.3),
        _tick("B", 0, 70.0, 72.0, market_p=0.4),
        _tick("C", 0, 72.0, 74.0, market_p=0.5),
        _tick("A", 20, 68.0, 70.0, market_p=0.2),
        _tick("B", 20, 70.0, 72.0, market_p=0.3),
        _tick("C", 20, 72.0, 74.0, market_p=0.5),
    ]

    groups = coherent_snapshot_groups(ticks, tick_minutes=10, min_buckets=3)

    assert len(groups) == 2
    assert [row.ticker for row in groups[0]] == ["A", "B", "C"]
    assert next(row for row in groups[0] if row.ticker == "A").market_p == 0.3
    assert {row.snapshot_ts.minute for row in groups[1]} == {20}


def test_boundary_distance_finds_containing_bucket():
    rows = [
        _tick("A", 0, 68.0, 70.0),
        _tick("B", 0, 70.0, 72.0),
        _tick("C", 0, 72.0, 74.0),
    ]

    dist, label = _boundary_distance(71.8, rows)

    assert dist == pytest.approx(0.2)
    assert label == "70-72"


def test_center_from_prob_map_uses_bucket_midpoints():
    rows = [
        _tick("A", 0, 68.0, 70.0),
        _tick("B", 0, 70.0, 72.0),
        _tick("C", 0, 72.0, 74.0),
    ]

    center = _center_from_prob_map(rows, {"A": 0.0, "B": 1.0, "C": 0.0})

    assert center == pytest.approx(71.0)


def test_classify_market_vs_metar_is_conservative():
    assert (
        _classify_market_vs_metar(4.0, move_10m=0.02, next_move_10m=0.20)
        == "market_moves_after_recent_metar"
    )
    assert (
        _classify_market_vs_metar(4.0, move_10m=0.20, next_move_10m=0.02)
        == "market_moved_before_or_at_recent_metar"
    )
    assert _classify_market_vs_metar(None, 0.2, 0.2) == "no_live_metar"
