"""
Expected value, fees, and sizing.

Kalshi fee formula (2026-04):
    fee_per_contract = ceil(0.07 * P * (1 - P) * 100) / 100   (in dollars)
    (equivalent to round up(0.07 * C * P * (1-P)) / C per contract)

We compute both YES-buy and NO-buy expected values and pick the better.
Sizing uses quarter-Kelly on the favorable side, capped by position limit.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from weather_bot.config import (
    BANKROLL_USD,
    KALSHI_FEE_COEFF,
    KELLY_FRACTION,
    MAX_POSITION_PCT,
    MIN_EDGE_BPS,
)

log = logging.getLogger(__name__)

# Auto-skip any signal where our fair diverges from market mid by >50 points.
# Two consecutive paper-trading losses on extreme-divergence signals (fair=44%
# vs mkt=1%, then fair=97% vs mkt=15%) proved the distribution is the outlier,
# not the market. When divergence is this large, something upstream is broken —
# trust the market and route the signal to manual review instead of trading it.
_MAX_FAIR_MKT_DIVERGENCE = 0.50


@dataclass
class Signal:
    ticker: str
    side: str                 # 'YES' | 'NO'
    fair_prob: float          # our probability of YES
    market_ask: float | None  # price to BUY YES ask
    market_bid: float | None  # price to SELL YES bid (== 1 - NO ask)
    edge: float               # per-contract $ edge after fees
    ev_per_dollar: float      # return per $ staked, after fees
    kelly_fraction: float
    size_usd: float
    action: str               # 'OPEN' | 'SKIP'
    notes: str = ""
    skip_reason: str | None = None  # 'DIVERGENCE' | 'NO_EDGE' | 'FEE_LOAD' | 'NO_BOOK' |
                                     # 'TRIPWIRE_RED' | 'BIAS_GATE' | 'AGREEMENT' | None
    model_votes: dict | None = None  # {"NBM":"YES","HRRR":"YES","GFS":"NO","n_yes":2,"n_no":1,...}


def fee_per_contract(price: float) -> float:
    """Kalshi rounds the per-order fee UP to the nearest cent; per-contract
    effective fee scales ~ linearly for small orders."""
    return math.ceil(KALSHI_FEE_COEFF * price * (1.0 - price) * 100) / 100.0


def kelly_fraction_optimal(p: float, b: float) -> float:
    """Standard Kelly for a binary bet paying net odds b at probability p.

    b = (1 - price) / price  when buying at `price` to win $1.
    Returns fraction of bankroll.
    """
    q = 1.0 - p
    if b <= 0:
        return 0.0
    f = (b * p - q) / b
    return max(0.0, f)


def evaluate(
    ticker: str,
    fair_prob: float,
    yes_ask: float | None,
    yes_bid: float | None,
    bankroll: float = BANKROLL_USD,
) -> Signal:
    """Produce a signal for a single Kalshi market given our fair probability."""
    p = float(fair_prob)
    best: Signal | None = None

    # Divergence guardrail — compute market mid (YES-side implied probability)
    # once before the loop. If our fair diverges by more than the threshold,
    # we'll force SKIP on both sides regardless of nominal edge.
    yes_mid: float | None = None
    if yes_ask is not None and yes_bid is not None:
        yes_mid = (float(yes_ask) + float(yes_bid)) / 2.0
    divergence_skip = False
    if yes_mid is not None and abs(p - yes_mid) > _MAX_FAIR_MKT_DIVERGENCE:
        divergence_skip = True
        log.warning(
            "DIVERGENCE guardrail tripped on %s: fair=%.3f mkt_mid=%.3f diff=%+.3f",
            ticker, p, yes_mid, p - yes_mid,
        )

    for side in ("YES", "NO"):
        # Kalshi NO is transacted by selling YES at bid (or buying NO at 1-yes_bid ask).
        if side == "YES":
            price = yes_ask
            win_prob = p
        else:
            price = None if yes_bid is None else (1.0 - yes_bid)
            win_prob = 1.0 - p
        if price is None or price <= 0 or price >= 1:
            continue

        fee = fee_per_contract(price)
        # Payout per contract if correct: $1.00. Profit: 1 - price - fee. Loss: price + fee.
        ev_contract = win_prob * (1.0 - price) - (1.0 - win_prob) * price - fee
        edge = ev_contract                              # in $ per contract
        ev_per_dollar = ev_contract / price             # return on capital

        b = (1.0 - price) / price
        k = kelly_fraction_optimal(win_prob, b) * KELLY_FRACTION
        k = min(k, MAX_POSITION_PCT)                   # risk cap

        size_usd = max(0.0, k * bankroll)
        edge_bps = int(edge * 10_000 / max(price, 1e-6))

        # Fee-sanity filter: Kalshi's fee floors at $0.01/contract, which crushes
        # penny trades (at price=$0.01, fee=$0.01 = 100% fee load). Skip any trade
        # where fees would exceed 20% of price — that's mechanically unprofitable
        # except at extreme edges, and the rounding error in the fee formula makes
        # our EV calc unreliable there anyway.
        fee_load = fee / price
        fee_ok = fee_load <= 0.20

        if divergence_skip:
            action, skip_reason = "SKIP", "DIVERGENCE"
        elif not fee_ok:
            action, skip_reason = "SKIP", "FEE_LOAD"
        elif not (edge > 0 and edge_bps >= MIN_EDGE_BPS and size_usd >= 1.0):
            action, skip_reason = "SKIP", "NO_EDGE"
        else:
            action, skip_reason = "OPEN", None

        note_extra = ""
        if divergence_skip and yes_mid is not None:
            note_extra = f" DIVERGENCE|fair={p:.3f}|mkt_mid={yes_mid:.3f}"

        sig = Signal(
            ticker=ticker,
            side=side,
            fair_prob=p,
            market_ask=yes_ask,
            market_bid=yes_bid,
            edge=edge,
            ev_per_dollar=ev_per_dollar,
            kelly_fraction=k,
            size_usd=size_usd,
            action=action,
            notes=f"side={side} price={price:.3f} fee={fee:.3f} fee_load={fee_load:.2f} edge_bps={edge_bps}{note_extra}",
            skip_reason=skip_reason,
        )
        if best is None or sig.edge > best.edge:
            best = sig

    if best is None:
        return Signal(ticker, "YES", p, yes_ask, yes_bid, 0.0, 0.0, 0.0, 0.0,
                      "SKIP", "no tradable side", skip_reason="NO_BOOK")
    return best
