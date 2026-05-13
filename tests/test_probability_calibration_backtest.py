from weather_bot.research.backtest_probability_calibration import (
    raw_probability_from_signal,
    side_won,
    simulated_entry_pnl,
)
from weather_bot.strategy.ev import Signal


def test_raw_probability_from_signal_prefers_cal_note():
    notes = "CAL|raw=0.742|cal=0.610|src=global side=YES"
    assert raw_probability_from_signal(0.61, notes) == 0.742


def test_raw_probability_from_signal_falls_back_to_fair_prob():
    assert raw_probability_from_signal(0.37, "side=YES") == 0.37


def test_side_won_is_side_relative():
    assert side_won("YES", 1.0) == 1.0
    assert side_won("YES", 0.0) == 0.0
    assert side_won("NO", 1.0) == 0.0
    assert side_won("NO", 0.0) == 1.0


def test_simulated_entry_pnl_only_counts_open_signals():
    sig = Signal(
        ticker="TEST",
        side="YES",
        fair_prob=0.8,
        market_ask=0.40,
        market_bid=0.35,
        edge=0.1,
        ev_per_dollar=0.25,
        kelly_fraction=0.02,
        size_usd=20.0,
        action="SKIP",
    )
    assert simulated_entry_pnl(sig, yes_won=1.0) == 0.0


def test_simulated_entry_pnl_uses_order_fee():
    sig = Signal(
        ticker="TEST",
        side="YES",
        fair_prob=0.8,
        market_ask=0.40,
        market_bid=0.35,
        edge=0.1,
        ev_per_dollar=0.25,
        kelly_fraction=0.02,
        size_usd=20.0,
        action="OPEN",
    )
    # 50 contracts at 40c. Mirrors the production floating-point fee helper.
    assert round(simulated_entry_pnl(sig, yes_won=1.0), 2) == 29.15
