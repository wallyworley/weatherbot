"""Unit tests for the EV / sizing engine."""
from weather_bot.strategy import ev


def test_fee_symmetric():
    # Fee peaks around 0.50 and is zero-ish at extremes.
    f50 = ev.fee_per_contract(0.5)
    f10 = ev.fee_per_contract(0.1)
    assert f50 > f10
    assert f10 >= 0


def test_fee_rounds_per_order_not_per_contract():
    one_contract_fee = ev.fee_per_contract(0.1)
    hundred_contract_fee = ev.fee_for_order(0.1, 100)
    assert hundred_contract_fee < one_contract_fee * 100
    assert ev.fee_per_contract(0.1, 100) == hundred_contract_fee / 100


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


def test_fee_aware_kelly_is_lower_than_fee_less_kelly():
    p = 0.60
    price = 0.50
    b = (1 - price) / price
    assert ev.kelly_fraction_with_fee(p, price, fee=0.02) < ev.kelly_fraction_optimal(p, b)


def test_evaluate_opens_when_edge_is_large():
    # fair 0.65 vs mid 0.495 = 0.155 divergence, within the 0.20 cap (tightened
    # from 0.50 on 2026-05-29). Still a large per-contract edge → OPEN YES.
    sig = ev.evaluate("KX-TEST", fair_prob=0.65, yes_ask=0.50, yes_bid=0.49, bankroll=1000)
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


def test_edge_haircut_can_flip_marginal_open_to_skip(monkeypatch):
    # Same input as test_evaluate_opens_when_edge_is_large (~13c/contract edge).
    # A 20c haircut exceeds the edge → the entry gate must SKIP for NO_EDGE.
    monkeypatch.setattr(ev, "_EDGE_HAIRCUT", 0.20)
    sig = ev.evaluate("KX-TEST", fair_prob=0.65, yes_ask=0.50, yes_bid=0.49, bankroll=1000)
    assert sig.action == "SKIP"
    assert sig.skip_reason == "NO_EDGE"


def test_edge_haircut_default_is_neutral():
    # Default (0.0) haircut leaves the large-edge case OPEN, unchanged.
    assert ev._EDGE_HAIRCUT == 0.0
    sig = ev.evaluate("KX-TEST", fair_prob=0.65, yes_ask=0.50, yes_bid=0.49, bankroll=1000)
    assert sig.action == "OPEN"
