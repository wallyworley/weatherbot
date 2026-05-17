from datetime import date, datetime, timezone

from weather_bot.strategy.ev import Signal
from weather_bot.strategy.profitability import apply_profitability_controls


def _signal(side="YES", price=0.40, size=20.0):
    market_ask = price if side == "YES" else 1.0 - price
    market_bid = 1.0 - price if side == "NO" else price - 0.01
    return Signal(
        ticker="TEST",
        side=side,
        fair_prob=0.70,
        market_ask=market_ask,
        market_bid=market_bid,
        edge=0.10,
        ev_per_dollar=0.25,
        kelly_fraction=0.02,
        size_usd=size,
        action="OPEN",
    )


def test_pauses_kmdw_when_live_mode(monkeypatch):
    # Paper-mode default now bypasses station pause for sample velocity.
    # Live mode (or paper with bypass disabled) still enforces the pause.
    import weather_bot.strategy.profitability as prof
    monkeypatch.setattr(prof, "PAPER_MODE", False)
    sig = apply_profitability_controls(
        _signal(), "KMDW", date(2026, 5, 7), datetime(2026, 5, 7, 14, tzinfo=timezone.utc)
    )
    assert sig.action == "SKIP"
    assert sig.skip_reason == "PROFIT_GATE"


def test_paper_mode_bypasses_station_pause(monkeypatch):
    import weather_bot.strategy.profitability as prof
    monkeypatch.setattr(prof, "PAPER_MODE", True)
    monkeypatch.setattr(prof, "PAPER_BYPASS_STATION_PAUSE", True)
    sig = apply_profitability_controls(
        _signal(), "KMDW", date(2026, 5, 7), datetime(2026, 5, 7, 14, tzinfo=timezone.utc)
    )
    assert sig.action == "OPEN"
    assert "PAUSE_BYPASS_PAPER" in sig.notes


def test_downsizes_knyc_lead_one():
    sig = apply_profitability_controls(
        _signal(price=0.20, size=20.0),
        "KNYC",
        date(2026, 5, 8),
        datetime(2026, 5, 7, 14, tzinfo=timezone.utc),
    )
    assert sig.action == "OPEN"
    assert sig.size_usd == 2.5


def test_blocks_weak_no_price_band():
    sig = apply_profitability_controls(
        _signal(side="NO", price=0.40, size=20.0),
        "KMIA",
        date(2026, 5, 7),
        datetime(2026, 5, 7, 14, tzinfo=timezone.utc),
    )
    assert sig.action == "SKIP"
    assert sig.skip_reason == "PROFIT_GATE"
