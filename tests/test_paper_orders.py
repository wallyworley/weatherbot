from datetime import datetime, timezone

from weather_bot.strategy.ev import Signal
from weather_bot.strategy.paper_orders import (
    entry_price_from_signal,
    executable_from_snapshot,
    fill_price_if_executable,
    maker_limit_price,
)


def _signal(side: str) -> Signal:
    return Signal(
        ticker="KXTEST",
        side=side,
        fair_prob=0.50,
        market_ask=0.42,
        market_bid=0.40,
        edge=0.05,
        ev_per_dollar=0.1,
        kelly_fraction=0.01,
        size_usd=10.0,
        action="OPEN",
    )


def test_maker_limit_uses_one_cent_improvement():
    assert maker_limit_price(0.42, 1) == 0.41
    assert maker_limit_price(0.01, 1) == 0.01


def test_entry_price_from_signal_handles_yes_and_no():
    assert entry_price_from_signal(_signal("YES")) == 0.42
    assert entry_price_from_signal(_signal("NO")) == 0.60


def test_executable_from_snapshot_uses_no_ask_when_present():
    snap = {
        "ts": datetime(2026, 5, 17, tzinfo=timezone.utc),
        "yes_bid": 0.40,
        "yes_bid_size": 9,
        "no_ask": 0.58,
        "no_ask_size": 7,
    }

    executable = executable_from_snapshot("NO", snap)

    assert executable is not None
    assert executable.price == 0.58
    assert executable.size == 7


def test_executable_from_snapshot_falls_back_to_yes_bid_for_no():
    snap = {
        "ts": datetime(2026, 5, 17, tzinfo=timezone.utc),
        "yes_bid": 0.40,
        "yes_bid_size": 9,
        "no_ask": None,
        "no_ask_size": None,
    }

    executable = executable_from_snapshot("NO", snap)

    assert executable is not None
    assert executable.price == 0.60
    assert executable.size == 9


def test_fill_requires_price_and_full_top_book_size():
    order = {
        "side": "YES",
        "limit_price": 0.41,
        "contracts": 8,
    }
    snap = {
        "ts": datetime(2026, 5, 17, tzinfo=timezone.utc),
        "yes_ask": 0.41,
        "yes_ask_size": 7,
    }
    assert fill_price_if_executable(order, snap) is None

    snap["yes_ask_size"] = 8
    assert fill_price_if_executable(order, snap) == 0.41
