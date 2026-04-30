"""Unit tests for the EV / sizing engine."""
from weather_bot.strategy import ev


def test_fee_symmetric():
    # Fee peaks around 0.50 and is zero-ish at extremes.
    f50 = ev.fee_per_contract(0.5)
    f10 = ev.fee_per_contract(0.1)
    assert f50 > f10
    assert f10 >= 0


def test_kelly_zero_when_no_edge():
    # Fair price == fair prob → zero edge before fees → Kelly = 0
    p = 0.5
    b = (1 - 0.5) / 0.5
    assert ev.kelly_fraction_optimal(p, b) == 0.0


def test_kelly_positive_when_edge():
    # 60% chance at 50 cents → strong edge
    p = 0.6
    b = (1 - 0.5) / 0.5
    f = ev.kelly_fraction_optimal(p, b)
    assert f > 0.15


def test_evaluate_opens_when_edge_is_large():
    sig = ev.evaluate("KX-TEST", fair_prob=0.70, yes_ask=0.50, yes_bid=0.49, bankroll=1000)
    assert sig.action == "OPEN"
    assert sig.side == "YES"
    assert sig.size_usd > 0


def test_evaluate_skips_when_no_edge():
    sig = ev.evaluate("KX-TEST", fair_prob=0.50, yes_ask=0.52, yes_bid=0.48, bankroll=1000)
    assert sig.action == "SKIP"


def test_evaluate_chooses_no_side_when_overpriced():
    # 20% fair, YES trading at 40 cents → better to buy NO at 60 cents
    sig = ev.evaluate("KX-TEST", fair_prob=0.20, yes_ask=0.40, yes_bid=0.38, bankroll=1000)
    assert sig.side == "NO"
