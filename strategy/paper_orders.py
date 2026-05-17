"""Pending paper-order execution model.

Paper mode should be conservative enough to teach us something. Instead of
turning every OPEN signal into an immediate fill, this module models a
maker-first order: place a limit below the current executable price, wait for
later snapshots to prove price and size were available, then write paper_fill.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from weather_bot.data import persistence
from weather_bot.strategy.ev import Signal, fee_for_order

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutableSnapshot:
    price: float
    size: int | None
    ts: datetime


@dataclass(frozen=True)
class ProcessSummary:
    checked: int = 0
    filled: int = 0
    expired: int = 0


def entry_price_from_signal(sig: Signal) -> float | None:
    if sig.side == "YES":
        return sig.market_ask
    if sig.market_bid is None:
        return None
    return 1.0 - sig.market_bid


def maker_limit_price(entry_price: float, improvement_cents: int) -> float:
    improvement = max(0, improvement_cents) / 100.0
    return round(max(0.01, float(entry_price) - improvement), 2)


def executable_from_snapshot(side: str, snapshot: dict) -> ExecutableSnapshot | None:
    ts = snapshot["ts"]
    if side == "YES":
        price = snapshot.get("yes_ask")
        size = snapshot.get("yes_ask_size")
    else:
        price = snapshot.get("no_ask")
        size = snapshot.get("no_ask_size")
        if price is None and snapshot.get("yes_bid") is not None:
            price = 1.0 - float(snapshot["yes_bid"])
            size = snapshot.get("yes_bid_size")
    if price is None:
        return None
    return ExecutableSnapshot(price=float(price), size=None if size is None else int(size), ts=ts)


def fill_price_if_executable(order: dict, snapshot: dict, require_size: bool = True) -> float | None:
    executable = executable_from_snapshot(order["side"], snapshot)
    if executable is None:
        return None
    if executable.price > float(order["limit_price"]):
        return None
    if require_size and executable.size is None:
        return None
    if executable.size is not None and executable.size < int(order["contracts"]):
        return None
    # A resting maker bid is credited at its limit, not a later lower ask, to
    # avoid over-crediting paper fills with price improvement we did not prove.
    return float(order["limit_price"])


def process_pending_orders(require_size: bool = True) -> ProcessSummary:
    pending = [dict(o) for o in persistence.list_pending_paper_orders()]
    filled = 0
    for order in pending:
        snapshots = persistence.list_market_snapshots_for_order(
            order["ticker"], order["created_at"], order["expires_at"]
        )
        for snapshot in snapshots:
            fill_price = fill_price_if_executable(order, dict(snapshot), require_size=require_size)
            if fill_price is None:
                continue
            contracts = int(order["contracts"])
            fees = fee_for_order(fill_price, contracts)
            fill_id = persistence.insert_paper_fill(
                dict(
                    signal_id=order["signal_id"],
                    ticker=order["ticker"],
                    side=order["side"],
                    price=fill_price,
                    contracts=contracts,
                    fees=fees,
                )
            )
            persistence.mark_paper_order_filled(
                int(order["id"]),
                fill_id,
                fill_price,
                snapshot["ts"],
            )
            filled += 1
            log.info(
                "PAPER ORDER FILLED %s %s @%.2f x%d from order %s",
                order["ticker"],
                order["side"],
                fill_price,
                contracts,
                order["id"],
            )
            break
    expired = persistence.expire_pending_paper_orders()
    return ProcessSummary(checked=len(pending), filled=filled, expired=expired)

