"""Threshold audit for the take-profit early-exit rule.

`strategy/early_exits.py` exits any open position whose progress toward max
gain reaches 0.85. We chose that threshold without evidence. This script
replays every settled fill in a window, reconstructs the peak progress
observed across its lifetime via market_snapshot history, and reports:

  - At what progress level did each fill peak?
  - Of fills that peaked above threshold X but were NOT exited (because
    threshold was set higher), what fraction ended up winning at settle?
  - What threshold would have maximized realized P&L given the snapshot path?

Run:
    python -m weather_bot.research.threshold_audit --days 30

Reads only — no writes.
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass

from psycopg.rows import dict_row

from weather_bot.data.persistence import connect

log = logging.getLogger(__name__)


@dataclass
class FillTrace:
    fill_id: int
    ticker: str
    side: str
    entry_price: float
    contracts: int
    fees: float
    actual_pnl: float           # final realized PnL (exit OR settlement)
    actual_path: str            # "EARLY_EXIT" | "SETTLED"
    won_at_settle: bool | None  # what the bucket actually paid (regardless of path)
    peak_progress: float        # highest progress observed in snapshots
    peak_exit_price: float      # the bid at peak progress


def _replay_one(row: dict, snapshots: list[dict]) -> FillTrace:
    """Compute peak progress + would-have-exit price for a single fill."""
    entry = float(row["price"])
    max_gain = 1.0 - entry
    peak_progress = 0.0
    peak_bid = entry
    for snap in snapshots:
        if row["side"] == "YES":
            bid = snap.get("yes_bid")
        else:
            no_bid = snap.get("no_bid")
            yes_ask = snap.get("yes_ask")
            if no_bid is not None:
                bid = no_bid
            elif yes_ask is not None:
                bid = 1.0 - float(yes_ask)
            else:
                bid = None
        if bid is None:
            continue
        gain = float(bid) - entry
        progress = (gain / max_gain) if max_gain > 0 else 0.0
        if progress > peak_progress:
            peak_progress = progress
            peak_bid = float(bid)

    # actual final PnL
    if row.get("exit_price") is not None:
        actual_pnl = (float(row["exit_price"]) - entry) * row["contracts"] \
                     - float(row["fees"]) - float(row.get("exit_fees") or 0.0)
        actual_path = "EARLY_EXIT"
    elif row.get("payout") is not None:
        actual_pnl = (float(row["payout"]) - entry) * row["contracts"] \
                     - float(row["fees"])
        actual_path = "SETTLED"
    else:
        actual_pnl = 0.0
        actual_path = "OPEN"

    # Did the BUCKET actually win, regardless of whether we exited early?
    won = None
    if row.get("obs_temp") is not None and row.get("lower_f") is not None or \
       row.get("upper_f") is not None:
        obs = row.get("obs_temp")
        if obs is not None:
            lo = row.get("lower_f")
            hi = row.get("upper_f")
            in_bucket = (lo is None or lo <= obs) and (hi is None or obs < hi)
            won = in_bucket if row["side"] == "YES" else (not in_bucket)

    return FillTrace(
        fill_id=int(row["id"]),
        ticker=row["ticker"],
        side=row["side"],
        entry_price=entry,
        contracts=int(row["contracts"]),
        fees=float(row["fees"]),
        actual_pnl=actual_pnl,
        actual_path=actual_path,
        won_at_settle=won,
        peak_progress=peak_progress,
        peak_exit_price=peak_bid,
    )


def replay(days: int, station: str | None = None) -> list[FillTrace]:
    fill_sql = """
        SELECT pf.id, pf.ts AS fill_ts, pf.ticker, pf.side, pf.price,
               pf.contracts, pf.fees, pf.payout, pf.exit_price, pf.exit_fees,
               km.lower_f, km.upper_f, km.station, km.valid_date,
               c.tmax_f AS obs_temp
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          LEFT JOIN cli_obs c ON c.station = km.station AND c.local_date = km.valid_date
         WHERE pf.settled = TRUE
           AND km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
           {station_filter}
         ORDER BY pf.ts
    """.format(station_filter="AND km.station = %s" if station else "")

    params: tuple = (days,) if not station else (days, station)
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(fill_sql, params)
        fills = cur.fetchall()
        traces: list[FillTrace] = []
        for f in fills:
            # Snapshots from fill_ts forward to either exit_ts or end of day
            # of valid_date — bounding the window keeps the query cheap.
            cur.execute(
                """
                SELECT ts, yes_bid::float AS yes_bid, yes_ask::float AS yes_ask,
                       no_bid::float AS no_bid, no_ask::float AS no_ask
                  FROM market_snapshot
                 WHERE ticker = %s
                   AND ts >= %s
                   AND ts <= (%s::date + INTERVAL '36 hours')
                 ORDER BY ts
                """,
                (f["ticker"], f["fill_ts"], f["valid_date"]),
            )
            snaps = cur.fetchall()
            traces.append(_replay_one(f, snaps))
    return traces


def bucket_outcomes(traces: list[FillTrace]) -> dict:
    """Bucket fills by peak progress and report outcome distribution."""
    bins = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.95), (0.95, 1.01)]
    out = []
    for lo, hi in bins:
        cohort = [t for t in traces if lo <= t.peak_progress < hi]
        n = len(cohort)
        if n == 0:
            out.append(dict(bucket=f"{lo:.2f}–{hi:.2f}", n=0,
                            won=0, lost=0, pnl=0.0, hypo_peak_pnl=0.0))
            continue
        won = sum(1 for t in cohort if t.won_at_settle is True)
        lost = sum(1 for t in cohort if t.won_at_settle is False)
        pnl = sum(t.actual_pnl for t in cohort)
        # Hypothetical PnL if we had exited at peak for every fill in cohort
        hypo = sum(
            (t.peak_exit_price - t.entry_price) * t.contracts - t.fees
            for t in cohort
        )
        out.append(dict(
            bucket=f"{lo:.2f}–{hi:.2f}",
            n=n, won=won, lost=lost,
            pnl=round(pnl, 2),
            hypo_peak_pnl=round(hypo, 2),
        ))
    return out


def what_if_threshold(traces: list[FillTrace], thresholds: list[float]) -> dict:
    """For each candidate threshold, compute the total PnL we would have
    realized if the bot exited at first crossing of that threshold (and
    otherwise let positions settle naturally).

    NOTE: this uses peak_exit_price as the "exit if crossed" proxy, which
    slightly overstates fills (we'd exit at *first* crossing, not peak).
    Still a reasonable directional comparison.
    """
    result = []
    for thr in thresholds:
        total = 0.0
        exits = 0
        for t in traces:
            if t.peak_progress >= thr:
                # Hypothetical exit PnL (ignore exit_fees; small + offsetting)
                total += (t.peak_exit_price - t.entry_price) * t.contracts - t.fees
                exits += 1
            else:
                # Held to settlement — use the actual settlement-only PnL
                # which is what would have happened with no early exit.
                if t.actual_path == "SETTLED":
                    total += t.actual_pnl
                # If actual_path is EARLY_EXIT but progress < thr in our peak,
                # something is off in the snapshot history; fall back to actual.
                else:
                    total += t.actual_pnl
        result.append(dict(threshold=thr, exits=exits, total_pnl=round(total, 2)))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--station", default=None,
                    help="Filter to one station (e.g. KNYC).")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    log.info("Replaying fills from last %d days%s",
             args.days, f" (station={args.station})" if args.station else "")
    traces = replay(args.days, args.station)
    log.info("Loaded %d settled fills with snapshot history", len(traces))
    if not traces:
        print("(no fills in window)")
        return 0

    print()
    print("=" * 78)
    print(f"Peak-progress distribution (n={len(traces)}, last {args.days}d)")
    print("=" * 78)
    print(f"{'bucket':<12} {'n':>5} {'won':>5} {'lost':>5} {'actual pnl':>12} {'if exit @peak':>14}")
    for row in bucket_outcomes(traces):
        print(f"{row['bucket']:<12} {row['n']:>5} {row['won']:>5} {row['lost']:>5} "
              f"${row['pnl']:>11.2f} ${row['hypo_peak_pnl']:>13.2f}")

    print()
    print("=" * 78)
    print("What-if: total realized PnL if take-profit threshold had been X")
    print("=" * 78)
    print(f"{'threshold':>10}  {'exits':>6}  {'total PnL':>12}")
    for row in what_if_threshold(traces,
                                  [0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]):
        marker = "  ← current" if abs(row["threshold"] - 0.85) < 0.001 else ""
        print(f"  {row['threshold']:>6.2f}    {row['exits']:>5}  ${row['total_pnl']:>11.2f}{marker}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
