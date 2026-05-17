from research.shadow_ensemble import (
    _shadow_distribution,
    ensemble_prob_between,
    normal_prob_between,
    normalize_weights,
)


def test_normal_prob_between_open_ended_bucket():
    p = normal_prob_between(mean=70.0, sigma=2.0, lo=70.0, hi=None)

    assert round(p, 3) == 0.5


def test_ensemble_prob_between_uses_member_counts():
    assert ensemble_prob_between([70.0, 72.0, 74.0, 76.0], 72.0, 76.0) == 0.5
    assert ensemble_prob_between([70.0, 72.0, 74.0, 76.0], None, 72.0) == 0.25
    assert ensemble_prob_between([], 72.0, 76.0) is None


def test_normalize_weights_drops_missing_models():
    weights = normalize_weights(
        {"NBM": 0.5, "GFS": 0.25, "ECMWF": 0.25},
        {"NBM": 70.0, "GFS": None, "ECMWF": 72.0},
    )

    assert round(weights["NBM"], 3) == 0.667
    assert "GFS" not in weights
    assert round(weights["ECMWF"], 3) == 0.333


def test_shadow_distribution_prefers_true_ensemble_members():
    row = {
        "lead_day": 1,
        "lower_f": 72.0,
        "upper_f": 76.0,
        "gfs_ens_members": [70.0] * 10 + [73.0] * 10 + [78.0] * 11,
        "ecmwf_ifs_ens_members": None,
        "ecmwf_aifs_ens_members": None,
        "nbm_p50": 100.0,
        "gfs_tmax": 100.0,
        "ecmwf_tmax": 100.0,
    }

    p_yes, mean, sigma, weights = _shadow_distribution(row)

    assert round(p_yes, 3) == round(10 / 31, 3)
    assert mean is not None
    assert sigma is not None
    assert weights == {"TRUE_GFS_ENS": 1.0}
