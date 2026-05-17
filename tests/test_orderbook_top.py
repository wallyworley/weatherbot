from main import _ask_size_for_side, _load_orderbook_top
from weather_bot.strategy.ev import Signal


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def get_orderbook(self, ticker):
        return self.payload


def _signal(side):
    return Signal(
        ticker="KXTEST",
        side=side,
        fair_prob=0.5,
        market_ask=0.40,
        market_bid=0.39,
        edge=0.1,
        ev_per_dollar=0.2,
        kelly_fraction=0.01,
        size_usd=10.0,
        action="OPEN",
    )


def test_load_orderbook_top_carries_executable_sizes_from_fp_payload():
    top = _load_orderbook_top(
        FakeClient(
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.3900", "15.0"], ["0.3800", "20.0"]],
                    "no_dollars": [["0.6000", "7.0"], ["0.5900", "40.0"]],
                }
            }
        ),
        "KXTEST",
    )

    assert top.yes_bid == 0.39
    assert top.yes_bid_size == 15
    assert top.yes_ask == 0.4
    assert top.yes_ask_size == 7
    assert _ask_size_for_side(_signal("YES"), top) == 7
    assert _ask_size_for_side(_signal("NO"), top) == 15
