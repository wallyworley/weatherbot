from weather_bot.research.morning_market_skill import (
    Row,
    _center_blend_probability,
    _parse_center_blend,
)


def _row(**overrides):
    data = {
        "ticker": "KXTEST",
        "ts": None,
        "station": "KNYC",
        "valid_date": None,
        "var": "TMAX_DAILY",
        "lower_f": 70.0,
        "upper_f": 75.0,
        "yes_win": 1,
        "model_p": 0.2,
        "raw_model_p": 0.2,
        "center_blend_p": None,
        "market_p": 0.25,
        "local_hour": 7,
        "market_bucket": "market:20-30%",
        "wind_octant": "wind:N",
        "temp_vs_nbm_bucket": "temp_vs_nbm:-1..+1F",
        "model_vote_bucket": "votes_yes:1_no:2",
        "risk_label": "risk:MEDIUM",
        "boundary_bucket": "boundary:<0.25",
        "nbm_p25": 68.0,
        "nbm_p50": 72.0,
        "nbm_p75": 76.0,
        "hrrr_tmax": 74.0,
        "gfs_tmax": 70.0,
    }
    data.update(overrides)
    return Row(**data)


def test_parse_center_blend_normalizes_names():
    assert _parse_center_blend("nbm=0.5, HRRR=0.25,gfs=0.25") == {
        "NBM": 0.5,
        "HRRR": 0.25,
        "GFS": 0.25,
    }


def test_center_blend_probability_drops_missing_points():
    row = _row(hrrr_tmax=None)
    p = _center_blend_probability(row, {"NBM": 0.5, "HRRR": 0.5})

    nbm_only = _center_blend_probability(row, {"NBM": 1.0})
    assert p == nbm_only
    assert 0.0 < p < 1.0

