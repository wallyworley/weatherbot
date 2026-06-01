from datetime import date, datetime, timezone

from weather_bot.strategy import profitability
from weather_bot.strategy.ev import Signal
from weather_bot.strategy.profitability import apply_profitability_controls

_NOW = datetime(2026, 5, 17, 14, tzinfo=timezone.utc)
_VD = date(2026, 5, 17)


def _signal(side="YES", yes_ask=0.20, yes_bid=0.19, size_usd=20.0):
    return Signal(
        ticker="KXTEST",
        side=side,
        fair_prob=0.5,
        market_ask=yes_ask,
        market_bid=yes_bid,
        edge=0.1,
        ev_per_dollar=0.5,
        kelly_fraction=0.02,
        size_usd=size_usd,
        action="OPEN",
    )


def test_yes_under_10c_is_blocked_by_default():
    sig = apply_profitability_controls(
        _signal(side="YES", yes_ask=0.05, size_usd=20.0),
        "KNYC",
        date(2026, 5, 17),
        datetime(2026, 5, 17, 14, tzinfo=timezone.utc),
    )

    assert sig.action == "SKIP"
    assert sig.skip_reason == "PROFIT_GATE"
    assert "YES_under_10c" in sig.notes


def test_yes_10_25c_is_capped():
    sig = apply_profitability_controls(
        _signal(side="YES", yes_ask=0.20, size_usd=30.0),
        "KNYC",
        date(2026, 5, 17),
        datetime(2026, 5, 17, 14, tzinfo=timezone.utc),
    )

    assert sig.action == "OPEN"
    # YES 10-25c cap was widened from $10 to $25 on 2026-05-17 to lift the
    # sample-velocity throttle on the only positive low-price sleeve. 30*0.5=15.
    assert sig.size_usd == 15.0
    assert "PROFIT_CAP" not in sig.notes  # 15 is now under the 25 cap


def test_no_under_50c_is_blocked_by_default():
    sig = apply_profitability_controls(
        _signal(side="NO", yes_bid=0.80, size_usd=20.0),
        "KNYC",
        date(2026, 5, 17),
        datetime(2026, 5, 17, 14, tzinfo=timezone.utc),
    )

    assert sig.action == "SKIP"
    assert sig.skip_reason == "PROFIT_GATE"
    assert "NO_under_50c" in sig.notes


def test_whitelist_blocks_station_not_on_it(monkeypatch):
    monkeypatch.setattr(profitability, "TRADE_STATION_WHITELIST", ["KAUS", "KBOS"])
    sig = apply_profitability_controls(
        _signal(side="YES", yes_ask=0.20, size_usd=30.0), "KPHX", _VD, _NOW,
    )
    assert sig.action == "SKIP"
    assert sig.skip_reason == "PROFIT_GATE"
    assert "not_whitelisted=KPHX" in sig.notes


def test_whitelist_allows_station_on_it(monkeypatch):
    # KBOS is whitelisted, so it passes the whitelist gate and trades (the
    # 10-25c band only caps size, it doesn't skip).
    monkeypatch.setattr(profitability, "TRADE_STATION_WHITELIST", ["KAUS", "KBOS"])
    sig = apply_profitability_controls(
        _signal(side="YES", yes_ask=0.20, size_usd=30.0), "KBOS", _VD, _NOW,
    )
    assert sig.action == "OPEN"
    assert "not_whitelisted" not in sig.notes


def test_empty_whitelist_is_disabled(monkeypatch):
    # Default (empty) whitelist must not restrict anything.
    monkeypatch.setattr(profitability, "TRADE_STATION_WHITELIST", [])
    sig = apply_profitability_controls(
        _signal(side="YES", yes_ask=0.20, size_usd=30.0), "KPHX", _VD, _NOW,
    )
    assert sig.action == "OPEN"
    assert "not_whitelisted" not in sig.notes
