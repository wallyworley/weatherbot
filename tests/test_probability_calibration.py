from weather_bot.strategy import probability_calibration as pc
from weather_bot.strategy.probability_calibration import (
    CalibrationStats,
    choose_stats,
    probability_bin,
    shrink_to_observed,
)


def test_probability_bin_is_decile_based():
    assert probability_bin(0.0) == 1
    assert probability_bin(0.099) == 1
    assert probability_bin(0.10) == 2
    assert probability_bin(0.999) == 10
    assert probability_bin(1.0) == 10


def test_shrink_to_observed_pulls_toward_bucket_result():
    calibrated, shrink, delta = shrink_to_observed(
        raw_prob=0.35,
        mean_pred=0.35,
        observed_freq=0.07,
        n=15,
        prior_n=25,
        max_delta=0.20,
    )
    assert round(shrink, 3) == 0.375
    assert calibrated < 0.35
    assert round(delta, 3) == -0.105


def test_shrink_to_observed_caps_large_adjustments():
    calibrated, _, delta = shrink_to_observed(
        raw_prob=0.50,
        mean_pred=0.50,
        observed_freq=0.0,
        n=100,
        prior_n=0,
        max_delta=0.20,
    )
    assert delta == -0.20
    assert calibrated == 0.30


def test_choose_stats_prefers_first_scope_with_enough_events():
    stats = choose_stats(
        [
            {"source": "station_lead", "n": 4.5, "mean_pred": 0.7, "observed_freq": 0.5},
            {"source": "lead", "n": 14.0, "mean_pred": 0.68, "observed_freq": 0.55},
            {"source": "global", "n": 30.0, "mean_pred": 0.65, "observed_freq": 0.60},
        ],
        min_n=12,
    )

    assert stats == CalibrationStats(
        source="lead",
        n=14.0,
        mean_pred=0.68,
        observed_freq=0.55,
    )


def test_calibrate_fair_probability_uses_hierarchical_stats(monkeypatch):
    def fake_bucket_stats(station, bin_id, days_back, lead_day=None):
        assert station == "KNYC"
        assert bin_id == 8
        assert lead_day == 1
        return CalibrationStats(
            source="station_lead",
            n=25.0,
            mean_pred=0.75,
            observed_freq=0.60,
        )

    monkeypatch.setattr(pc, "_bucket_stats", fake_bucket_stats)

    result = pc.calibrate_fair_probability("KNYC", 0.76, lead_day=1)

    assert result.applied
    assert result.source == "station_lead"
    assert result.n == 25
    assert result.calibrated_prob < result.raw_prob
