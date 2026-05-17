from research.ensemble_calibration import calibrated_member_probability, choose_best_variant


def test_calibrated_member_probability_spread_changes_tail_probability():
    members = [70.0] * 20 + [72.0] * 20 + [74.0] * 20

    narrow = calibrated_member_probability(members, 78.0, None, spread_multiplier=1.0)
    wide = calibrated_member_probability(members, 78.0, None, spread_multiplier=3.0)

    assert wide > narrow


def test_choose_best_variant_prefers_warm_bias_when_outcomes_are_warm_tail():
    rows = []
    for i in range(20):
        rows.append(
            {
                "signal_id": i,
                "ts": i,
                "obs_tmax": 79.0,
                "lower_f": 78.0,
                "upper_f": None,
                "gfs_ens_members": [74.0] * 30 + [75.0] * 30,
                "ecmwf_ifs_ens_members": None,
                "ecmwf_aifs_ens_members": None,
                "weathernext2_members": None,
            }
        )

    bias, spread, score = choose_best_variant(rows)

    assert bias > 0
    assert spread >= 1.0
    assert score < 1.0
