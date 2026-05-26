"""Early exit take-profit execution module.

Three guards (added 2026-05-26) make paper P&L closer to what live trading
would realistically capture:

  1. MAX_SNAPSHOT_AGE_SECONDS — refuse to exit on a stale quote. The
     market_snapshot we read for the exit_price might be N seconds old
     (depends on snapshot poll cadence). If the quote is older than this
     limit we skip; the next main.py tick will re-check with a fresh quote.

  2. REQUIRE_BOOK_SIZE — only exit when the top-of-book bid has enough
     contracts to absorb our full position. In live trading, a small bid
     means partial fills (which we don't model in paper). Strict size check
     gives paper P&L the same liquidity constraint as live execution.

  3. SLIPPAGE_HAIRCUT_CENTS — deduct ¢ from the recorded exit_price.
     Live execution can't always hit the observed bid (the bid may pull
     before our order arrives, or we walk down a thin book). A 2¢ haircut
     is a conservative estimate based on Kalshi's typical tick depth.

All three are env-configurable so we can tune as we learn.

Retry is implicit: main.py runs every 5 min, so a position that fails any
of these checks gets re-evaluated next tick. Partial fills are NOT modeled
— it's all-or-nothing per the design note in strategy/early_exits.md (TODO).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from psycopg.rows import dict_row

from weather_bot.data import persistence
from weather_bot.strategy import ev

log = logging.getLogger(__name__)


# Env-configurable guards. Defaults chosen conservatively; tighten or
# loosen by setting the corresponding env var in /opt/weather_bot/.env.
_MAX_SNAPSHOT_AGE_SECONDS = int(os.getenv("EARLY_EXIT_MAX_SNAPSHOT_AGE_S", "60"))
_SLIPPAGE_HAIRCUT_CENTS   = int(os.getenv("EARLY_EXIT_SLIPPAGE_CENTS",     "2"))
_REQUIRE_BOOK_SIZE        = os.getenv("EARLY_EXIT_REQUIRE_BOOK_SIZE", "true").lower() == "true"


@dataclass(frozen=True)
class ExitSummary:
    checked: int = 0
    exited: int = 0
    skipped_stale: int = 0
    skipped_thin_book: int = 0


def process_early_exits(threshold: float = 0.85) -> ExitSummary:
    """Scan open paper fills and execute early-exit take-profit trades.

    For each open fill:
      1. Pull the latest market_snapshot for the ticker.
      2. Reject if the snapshot is older than MAX_SNAPSHOT_AGE_SECONDS.
      3. Compute exit_bid for the position's side.
      4. Reject if top-of-book size < contracts (require_book_size).
      5. Compute progress against max gain; if >= threshold:
         - Apply slippage haircut to exit_bid.
         - Close the paper fill with reason TAKE_PROFIT_<threshold>.
    """
    sql = """
    WITH open_fills AS (
        SELECT pf.id, pf.ticker, pf.side, pf.ts, pf.price, pf.contracts, pf.fees,
               km.station, km.valid_date, km.lower_f, km.upper_f
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = FALSE
    )
    SELECT f.*,
           ms.ts AS snapshot_ts,
           ms.yes_bid::float       AS yes_bid,
           ms.yes_ask::float       AS yes_ask,
           ms.yes_bid_size::int    AS yes_bid_size,
           ms.yes_ask_size::int    AS yes_ask_size,
           ms.no_bid::float        AS no_bid,
           ms.no_ask::float        AS no_ask,
           ms.no_bid_size::int     AS no_bid_size,
           ms.no_ask_size::int     AS no_ask_size
      FROM open_fills f
      LEFT JOIN LATERAL (
          SELECT *
            FROM market_snapshot ms
           WHERE ms.ticker = f.ticker
           ORDER BY ms.ts DESC
           LIMIT 1
      ) ms ON true
     ORDER BY f.station, f.valid_date, f.ticker
    """

    checked = 0
    exited = 0
    skipped_stale = 0
    skipped_thin_book = 0
    now = datetime.now(tz=timezone.utc)

    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    for r in rows:
        checked += 1

        # Guard 1: snapshot freshness ------------------------------------
        snap_ts = r.get("snapshot_ts")
        if snap_ts is None:
            continue  # never had a quote — nothing to exit against
        age_s = (now - snap_ts).total_seconds()
        if age_s > _MAX_SNAPSHOT_AGE_SECONDS:
            skipped_stale += 1
            log.debug(
                "early-exit skip %s: snapshot age %.0fs > %ds",
                r["ticker"], age_s, _MAX_SNAPSHOT_AGE_SECONDS,
            )
            continue

        # Determine the bid we'd hit and the available size at that level
        if r["side"] == "YES":
            exit_bid = r.get("yes_bid")
            avail_size = r.get("yes_bid_size")
        else:
            # Selling a NO is buying YES — we hit the no_bid (or derive
            # from yes_ask if no_bid isn't quoted). Size at that level is
            # no_bid_size; the yes_ask fallback uses yes_ask_size as the
            # next-best proxy.
            if r.get("no_bid") is not None:
                exit_bid = r["no_bid"]
                avail_size = r.get("no_bid_size")
            elif r.get("yes_ask") is not None:
                exit_bid = 1.0 - float(r["yes_ask"])
                avail_size = r.get("yes_ask_size")
            else:
                exit_bid = None
                avail_size = None

        if exit_bid is None:
            continue

        # Guard 2: book size --------------------------------------------
        contracts = int(r["contracts"])
        if _REQUIRE_BOOK_SIZE and avail_size is not None:
            if avail_size < contracts:
                skipped_thin_book += 1
                log.debug(
                    "early-exit skip %s: top-of-book size %d < contracts %d",
                    r["ticker"], avail_size, contracts,
                )
                continue

        # Progress check -------------------------------------------------
        entry_price = float(r["price"])
        max_gain = 1.0 - entry_price
        gain = float(exit_bid) - entry_price
        progress = gain / max_gain if max_gain > 0 else 0.0
        if progress < threshold:
            continue

        # Guard 3: slippage haircut on logged exit price -----------------
        # Kalshi prices in dollars; cents → 0.01 each.
        # Floor at 0.01 (1 tick above zero) so we never log a negative-or-
        # zero exit_price after the haircut.
        haircut = _SLIPPAGE_HAIRCUT_CENTS / 100.0
        effective_exit = max(0.01, float(exit_bid) - haircut)

        entry_fees = float(r["fees"])
        exit_fees = ev.fee_for_order(effective_exit, contracts)
        persistence.close_paper_fill_early(
            int(r["id"]),
            exit_price=effective_exit,
            exit_fees=exit_fees,
            exit_snapshot_ts=snap_ts,
            exit_reason=f"TAKE_PROFIT_{threshold:.2f}",
        )
        exited += 1

        gross_pnl = (effective_exit - entry_price) * contracts
        net_pnl = gross_pnl - entry_fees - exit_fees
        log.info(
            "TAKE PROFIT TRIGGERED: Fill %d %s %s early-exited @%.3f "
            "(observed bid %.3f, haircut %d¢, entry %.3f) progress=%.1f%% "
            "contracts=%d (book size=%s, snap age=%.0fs) | "
            "Gross=$%+.2f EntryFees=$%.2f ExitFees=$%.2f Net=$%+.2f",
            r["id"], r["ticker"], r["side"],
            effective_exit, float(exit_bid), _SLIPPAGE_HAIRCUT_CENTS,
            entry_price, progress * 100.0, contracts,
            avail_size if avail_size is not None else "?", age_s,
            gross_pnl, entry_fees, exit_fees, net_pnl,
        )

    if skipped_stale or skipped_thin_book:
        log.info(
            "early-exit guards: stale_snapshot=%d thin_book=%d (checked=%d, exited=%d)",
            skipped_stale, skipped_thin_book, checked, exited,
        )

    return ExitSummary(
        checked=checked, exited=exited,
        skipped_stale=skipped_stale, skipped_thin_book=skipped_thin_book,
    )
