"""Early exit take-profit execution module."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from psycopg.rows import dict_row

from weather_bot.data import persistence
from weather_bot.strategy import ev

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitSummary:
    checked: int = 0
    exited: int = 0


def process_early_exits(threshold: float = 0.85) -> ExitSummary:
    """Scan open paper fills and execute early exit take-profit trades.

    For each open fill:
      1. Retrieve the latest market snapshot for its ticker.
      2. Determine exit price (exit_bid).
      3. Calculate profit progress relative to max gain.
      4. If progress >= threshold, settle the paper fill early and log the results.
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
           ms.yes_bid::float AS yes_bid,
           ms.yes_ask::float AS yes_ask,
           ms.no_bid::float AS no_bid,
           ms.no_ask::float AS no_ask
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

    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    for r in rows:
        checked += 1
        # Determine best executable exit price
        exit_bid: float | None = None
        if r["side"] == "YES":
            exit_bid = r["yes_bid"]
        else:
            # Sell NO side
            if r["no_bid"] is not None:
                exit_bid = r["no_bid"]
            elif r["yes_ask"] is not None:
                exit_bid = 1.0 - r["yes_ask"]

        if exit_bid is None:
            continue

        entry_price = float(r["price"])
        max_gain = 1.0 - entry_price
        gain = exit_bid - entry_price

        # Progress toward max gain
        progress = gain / max_gain if max_gain > 0 else 0.0
        if progress >= threshold:
            contracts = int(r["contracts"])
            entry_fees = float(r["fees"])
            exit_fees = ev.fee_for_order(exit_bid, contracts)
            persistence.close_paper_fill_early(
                int(r["id"]),
                exit_price=exit_bid,
                exit_fees=exit_fees,
                exit_snapshot_ts=r["snapshot_ts"],
                exit_reason=f"TAKE_PROFIT_{threshold:.2f}",
            )
            exited += 1

            # Log transaction details
            gross_pnl = (exit_bid - entry_price) * contracts
            net_pnl = gross_pnl - entry_fees - exit_fees
            log.info(
                "TAKE PROFIT TRIGGERED: Fill %d %s %s early-exited @%.2f (entry %.2f) progress=%.1f%% contracts=%d | Gross=$%+.2f EntryFees=$%.2f ExitFees=$%.2f Net=$%+.2f",
                r["id"],
                r["ticker"],
                r["side"],
                exit_bid,
                entry_price,
                progress * 100.0,
                contracts,
                gross_pnl,
                entry_fees,
                exit_fees,
                net_pnl,
            )

    return ExitSummary(checked=checked, exited=exited)
