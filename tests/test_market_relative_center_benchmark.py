from datetime import date, datetime, timezone

from weather_bot.research.market_relative_center_benchmark import (
    BucketRow,
    _crps_discrete,
    _normalize,
    score_event,
)


def _row(lower, upper, truth, yes_win, model_p, market_p):
    return BucketRow(
        station="KNYC",
        valid_date=date(2026, 6, 1),
        var="TMAX_DAILY",
        lead_day=0,
        ticker=f"KX-{lower}-{upper}",
        ts=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        lower_f=lower,
        upper_f=upper,
        truth_f=truth,
        yes_win=yes_win,
        model_p=model_p,
        market_p=market_p,
    )


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
