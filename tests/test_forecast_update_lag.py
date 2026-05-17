import pytest

from research.forecast_update_lag import _freshest_source, _signed_move, edge_z_score


def test_signed_move_is_positive_when_market_moves_toward_edge():
    assert _signed_move(0.10, future_mid=0.55, current_mid=0.50) == pytest.approx(0.05)
    assert _signed_move(-0.10, future_mid=0.45, current_mid=0.50) == pytest.approx(0.05)
    assert _signed_move(-0.10, future_mid=0.55, current_mid=0.50) == pytest.approx(-0.05)


def test_freshest_source_ignores_missing_and_negative_ages():
    source, age = _freshest_source({"NBM": None, "GFS": 20.0, "ECMWF": -1.0})

    assert source == "GFS"
    assert age == 20.0


def test_edge_z_score_uses_error_floor():
    assert edge_z_score(0.10, error_floor=0.05) == 2.0
