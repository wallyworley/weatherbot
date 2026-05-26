"""Backtest: would a 'forecast-cooled exit' rule have improved PnL?

Concept: while a paper fill is open, watch every fresh NBM forecast cycle.
If the cycle's p50 has moved *against* the position by more than a chosen
threshold (in °F), exit at the next available market_snapshot bid.

"Against the position" depends on the bucket shape and side:

  Two-sided bucket [lo, hi)   YES wins if p50 stays close to bucket center
                               NO  wins if p50 moves away from center
  One-sided low (T-bucket)    YES wins if p50 stays low
                               NO  wins if p50 stays high
  One-sided high (B-bucket)   YES wins if p50 stays high
                               NO  wins if p50 stays low

This script replays each settled fill, finds the entry-time NBM p50, walks
forward through every later NBM cycle (4/day at 0,6,12,18 UTC), measures
the adverse move per cycle, and figures out the first crossing per
candidate threshold. Then it picks the bid from the closest market_snapshot
after that crossing as the would-have-exit price.

Output is the same shape as threshold_audit.py: a what-if PnL table by
threshold. Read-only.

Run:
    python -m weather_bot.research.forecast_exit_audit --days 30
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field

from psycopg.rows import dict_row

from weather_bot.data.persistence import connect

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bucket geometry
# ---------------------------------------------------------------------------
def adverse_signed_delta(side: str, lower_f, upper_f,
                          entry_p50: float, new_p50: float) -> float:
    """Return how many °F the forecast has moved *against* the position.

    Positive return value = adverse (worse for the holder).
    Negative return value = favorable (better for the holder).

    Definition is "increase in distance from the bucket's target center"
    for YES bets, inverted for NO bets. One-sided buckets use the direction
    of cooling/warming directly.
    """
    if side not in ("YES", "NO"):
        return 0.0

    # One-sided low: "T58" → upper_f set (exclusive), lower_f None.
    # YES wins if obs < (upper_f - 1). Adverse direction: warming.
    if lower_f is None and upper_f is not None:
        # Warming (new > entry) is adverse for YES, favorable for NO.
        delta = new_p50 - entry_p50  # positive = warming
        return delta if side == "YES" else -delta

    # One-sided high: "B89" → lower_f set (inclusive), upper_f None.
    # YES wins if obs >= lower_f. Adverse direction: cooling.
    if upper_f is None and lower_f is not None:
        delta = new_p50 - entry_p50  # positive = warming
        return -delta if side == "YES" else delta

    # Two-sided range: parser stores upper_f as (hi + 1). Center for win =
    # midpoint of [lo, hi].
    if lower_f is not None and upper_f is not None:
        center = (float(lower_f) + float(upper_f) - 1.0) / 2.0
        d_old = abs(entry_p50 - center)
        d_new = abs(new_p50 - center)
        # YES: increasing distance from center is adverse.
        # NO: decreasing distance is adverse (bucket more likely to hit).
        delta = d_new - d_old
        return delta if side == "YES" else -delta

    return 0.0


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
@dataclass
class FillReplay:
    fill_id: int
    ticker: str
    side: str
    entry_price: float
    contracts: int
    fees: float
    entry_p50: float
    actual_pnl: float
    actual_path: str  # EARLY_EXIT | SETTLED
    # Map threshold -> hypothetical PnL under that forecast-exit rule.
    threshold_pnl: dict[float, float] = field(default_factory=dict)
    threshold_exits: dict[float, bool] = field(default_factory=dict)


def _load_entry_p50(cur, station: str, valid_date, fill_ts) -> float | None:
    """NBM p50 for (station, valid_date) from the most recent cycle issued
    at or before fill_ts. This is what the bot's entry signal saw."""
    cur.execute(
        """
        SELECT value
          FROM prob_forecast
         WHERE station = %s AND valid_date = %s
           AND var = 'TMAX_DAILY' AND percentile = 50
           AND run_time <= %s
         ORDER BY run_time DESC LIMIT 1
        """,
        (station, valid_date, fill_ts),
    )
    row = cur.fetchone()
    return float(row["value"]) if row else None


def _load_subsequent_cycles(cur, station: str, valid_date, fill_ts):
    """All NBM p50 cycles for this (station, valid_date) issued after fill_ts."""
    cur.execute(
        """
        SELECT run_time, value AS p50
          FROM prob_forecast
         WHERE station = %s AND valid_date = %s
           AND var = 'TMAX_DAILY' AND percentile = 50
           AND run_time > %s
         ORDER BY run_time ASC
        """,
        (station, valid_date, fill_ts),
    )
    return cur.fetchall()


def _bid_at(cur, ticker: str, side: str, around_ts) -> float | None:
    """First market_snapshot at or after `around_ts` for the ticker, returning
    the executable exit bid for the position side."""
    cur.execute(
        """
        SELECT yes_bid::float AS yes_bid,
               yes_ask::float AS yes_ask,
               no_bid::float AS no_bid
          FROM market_snapshot
         WHERE ticker = %s AND ts >= %s
         ORDER BY ts ASC LIMIT 1
        """,
        (ticker, around_ts),
    )
    snap = cur.fetchone()
    if snap is None:
        return None
    if side == "YES":
        return snap.get("yes_bid")
    if snap.get("no_bid") is not None:
        return snap.get("no_bid")
    if snap.get("yes_ask") is not None:
        return 1.0 - float(snap["yes_ask"])
    return None


def replay(days: int, thresholds: list[float]) -> list[FillReplay]:
    fill_sql = """
        SELECT pf.id, pf.ts AS fill_ts, pf.ticker, pf.side, pf.price,
               pf.contracts, pf.fees, pf.payout, pf.exit_price, pf.exit_fees,
               km.lower_f, km.upper_f, km.station, km.valid_date
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = TRUE
           AND km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
         ORDER BY pf.ts
    """
    out: list[FillReplay] = []
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(fill_sql, (days,))
        fills = cur.fetchall()
        log.info("Loaded %d settled fills", len(fills))

        for f in fills:
            entry_p50 = _load_entry_p50(cur, f["station"], f["valid_date"],
                                          f["fill_ts"])
            if entry_p50 is None:
                continue
            cycles = _load_subsequent_cycles(cur, f["station"], f["valid_date"],
                                              f["fill_ts"])

            entry = float(f["price"])
            contracts = int(f["contracts"])
            entry_fees = float(f["fees"])

            # Actual final PnL
            if f.get("exit_price") is not None:
                actual_pnl = (float(f["exit_price"]) - entry) * contracts \
                             - entry_fees - float(f.get("exit_fees") or 0)
                actual_path = "EARLY_EXIT"
            else:
                actual_pnl = (float(f.get("payout") or 0) - entry) * contracts \
                             - entry_fees
                actual_path = "SETTLED"

            rep = FillReplay(
                fill_id=int(f["id"]), ticker=f["ticker"], side=f["side"],
                entry_price=entry, contracts=contracts, fees=entry_fees,
                entry_p50=entry_p50, actual_pnl=actual_pnl, actual_path=actual_path,
            )

            # For each threshold, find first cycle where adverse move crossed
            for thr in thresholds:
                exited = False
                hypo_pnl = actual_pnl  # fallback if never crosses
                for c in cycles:
                    adverse = adverse_signed_delta(
                        f["side"], f["lower_f"], f["upper_f"],
                        entry_p50, float(c["p50"]),
                    )
                    if adverse >= thr:
                        # Find a market bid at/after this cycle's run_time
                        bid = _bid_at(cur, f["ticker"], f["side"], c["run_time"])
                        if bid is None:
                            # No snapshot data — keep actual outcome
                            break
                        # Approximate exit fees with the same Kalshi formula
                        # the bot uses (no separate exit_fee_for_order import
                        # to keep this script self-contained).
                        from math import ceil
                        exit_fees = ceil(0.07 * contracts * bid * (1.0 - bid) * 100) / 100.0
                        hypo_pnl = (bid - entry) * contracts - entry_fees - exit_fees
                        exited = True
                        break
                rep.threshold_pnl[thr] = round(hypo_pnl, 2)
                rep.threshold_exits[thr] = exited

            out.append(rep)
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def summarize(replays: list[FillReplay], thresholds: list[float]) -> None:
    n = len(replays)
    actual_total = sum(r.actual_pnl for r in replays)
    print()
    print("=" * 78)
    print(f"Forecast-cooled-exit backtest ({n} fills)")
    print("=" * 78)
    print(f"Actual total PnL (with current rules, no forecast exit): ${actual_total:.2f}")
    print()
    print(f"{'threshold (°F)':>16}  {'exits triggered':>16}  {'total PnL':>12}  {'vs actual':>12}")
    for thr in thresholds:
        thr_total = sum(r.threshold_pnl.get(thr, r.actual_pnl) for r in replays)
        exits = sum(1 for r in replays if r.threshold_exits.get(thr, False))
        delta = thr_total - actual_total
        print(f"  {thr:>10.1f} °F     {exits:>16}  ${thr_total:>11.2f}  ${delta:>+11.2f}")

    # Also: how many positions saw ANY adverse move at all?
    print()
    print("Adverse-move occurrence per fill (any cycle):")
    saw_any_move = sum(1 for r in replays if any(r.threshold_exits.values()))
    print(f"  {saw_any_move}/{n} fills saw an adverse move >= 1°F at some point")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--thresholds", default="1.0,2.0,3.0,4.0,5.0",
                    help="Comma-separated thresholds in °F.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    thresholds = [float(x) for x in args.thresholds.split(",")]
    replays = replay(args.days, thresholds)
    if not replays:
        print("(no replayable fills)")
        return 0
    summarize(replays, thresholds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
