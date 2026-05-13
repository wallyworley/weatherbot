from research.shadow_ensemble import normal_prob_between, normalize_weights


def test_normal_prob_between_open_ended_bucket():
    p = normal_prob_between(mean=70.0, sigma=2.0, lo=70.0, hi=None)

    assert round(p, 3) == 0.5


def test_normalize_weights_drops_missing_models():
    weights = normalize_weights(
        {"NBM": 0.5, "GFS": 0.25, "ECMWF": 0.25},
        {"NBM": 70.0, "GFS": None, "ECMWF": 72.0},
    )

    assert round(weights["NBM"], 3) == 0.667
    assert "GFS" not in weights
    assert round(weights["ECMWF"], 3) == 0.333
