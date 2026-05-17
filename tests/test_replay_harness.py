from datetime import date, datetime, timezone

from weather_bot.research.replay_harness import (
    ReplayRow,
    StratumSummary,
    _delta_vs_original,
    _stratify,
)


def _row(**kw):
    defaults = dict(
        fill_id=1,
        ts=datetime(2026, 5, 1, 14, tzinfo=timezone.utc),
        ticker="T1", event_ticker="E1", station="KNYC", var="TMAX_DAILY",
        valid_date=date(2026, 5, 1), lead_day=0, nbm_cycle_hour=12,
        side="YES", price=0.40, contracts=10, fees=0.20,
        payout=10.0, won=True,
        p_side_original=0.55, p_side_pit=0.60, edge_pit=0.02, expected_pnl_pit=0.20,
    )
    defaults.update(kw)
    return ReplayRow(**defaults)


def test_stratum_brier_and_calibration():
    s = StratumSummary()
    s.add(_row(p_side_pit=0.60, won=True))
    s.add(_row(p_side_pit=0.60, won=False))
    d = s.as_dict()
    assert d["n"] == 2
    # Brier: ((0.6-1)^2 + (0.6-0)^2)/2 = (0.16 + 0.36)/2 = 0.26
    assert abs(d["brier"] - 0.26) < 1e-9
    assert d["predicted_win_rate"] == 0.60
    assert d["observed_win_rate"] == 0.50
    assert abs(d["calibration_error"] - 0.10) < 1e-9


def test_stratify_buckets_by_station_and_lead():
    rows = [
        _row(station="KNYC", lead_day=0),
        _row(station="KNYC", lead_day=1),
        _row(station="KMIA", lead_day=0),
    ]
    s = _stratify(rows)
    assert s["by_station"]["KNYC"].n == 2
    assert s["by_station"]["KMIA"].n == 1
    assert s["by_lead"]["L0"].n == 2
    assert s["by_lead"]["L1"].n == 1
    assert s["by_station_lead"]["KNYC/L1"].n == 1


def test_delta_vs_original_handles_missing_original():
    rows = [
        _row(p_side_pit=0.6, p_side_original=0.5, won=True),
        _row(p_side_pit=0.4, p_side_original=None, won=False),  # ignored
    ]
    d = _delta_vs_original(rows)
    assert d["n_paired"] == 1
    assert abs(d["brier_pit"] - 0.16) < 1e-9
    assert abs(d["brier_original"] - 0.25) < 1e-9
    assert d["brier_delta"] < 0  # PIT better in this fake


def test_stratum_skips_rows_without_pit_prob():
    s = StratumSummary()
    s.add(_row(p_side_pit=None))
    assert s.n == 0
