"""Cross-bucket smile-arbitrage scanner.

For any Kalshi event whose markets partition the temperature axis into
mutually-exclusive, collectively-exhaustive buckets, the sum of the YES
"true probabilities" must equal 1.0. The market-implied YES probability per
bucket can be approximated by the YES mid (yes_ask + yes_bid) / 2.

Two diagnostics per event:

  over_round = sum_i yes_mid_i
    over_round > 1  → market over-pays YES (typical: fees + spread)
    over_round < 1  → market over-pays NO

  per-bucket mispricing = (yes_mid_i / over_round) - yes_mid_i
    positive  → bucket is underpriced (buy YES is the renormalized direction)
    negative  → bucket is overpriced  (buy NO  is the renormalized direction)

Edge in this signal is *independent of forecast skill*: it relies only on
the algebraic constraint that the joint distribution sums to 1. Settlement
risk and execution risk (depth, snapshot age, fees) still apply.

Outputs a markdown report in research/reports/.

Usage:
    .venv/bin/python -m weather_bot.jobs.smile_arbitrage_scan
    .venv/bin/python -m weather_bot.jobs.smile_arbitrage_scan --min-mispricing 0.04 --max-snapshot-age-min 10
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg.rows import dict_row

from weather_bot.config import ACTIVE_TRADE_STATIONS
from weather_bot.data import persistence
from weather_bot.strategy.ev import fee_for_order

log = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "research" / "reports"


@dataclass
class BucketQuote:
    ticker: str
    lower_f: float | None
    upper_f: float | None
    yes_ask: float | None
    yes_bid: float | None
    yes_ask_size: int | None
    yes_bid_size: int | None
    snapshot_ts: datetime | None
    status: str | None

    @property
    def yes_mid(self) -> float | None:
        if self.yes_ask is None or self.yes_bid is None:
            return None
        return (float(self.yes_ask) + float(self.yes_bid)) / 2.0

    @property
    def label(self) -> str:
        lo = "≤" if self.lower_f is None else f"{self.lower_f:.0f}"
        hi = "+" if self.upper_f is None else f"<{self.upper_f:.0f}"
        return f"[{lo},{hi})"


@dataclass
class EventScan:
    event_ticker: str
    station: str
    var: str
    valid_date: object
    over_round: float
    bucket_count: int
    quotes_complete: int
    buckets: list[tuple[BucketQuote, float, float]]  # (quote, normalized_share, mispricing)

    @property
    def max_abs_mispricing(self) -> float:
        return max((abs(m) for _, _, m in self.buckets), default=0.0)


def _latest_snapshots(station_filter: list[str] | None) -> dict[str, BucketQuote]:
    """Return the most recent snapshot per ticker for active/open markets."""
    sql = """
    WITH latest AS (
        SELECT DISTINCT ON (ms.ticker)
               ms.ticker, ms.ts, ms.yes_ask, ms.yes_bid,
               ms.yes_ask_size, ms.yes_bid_size, ms.status
          FROM market_snapshot ms
          JOIN kalshi_market km ON km.ticker = ms.ticker
         WHERE km.status IN ('open', 'active')
           AND km.valid_date >= CURRENT_DATE
           AND (%(stations)s::text[] IS NULL OR km.station = ANY(%(stations)s))
         ORDER BY ms.ticker, ms.ts DESC
    )
    SELECT l.*, km.lower_f, km.upper_f
      FROM latest l
      JOIN kalshi_market km ON km.ticker = l.ticker
    """
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"stations": station_filter})
        out: dict[str, BucketQuote] = {}
        for r in cur.fetchall():
            out[r["ticker"]] = BucketQuote(
                ticker=r["ticker"],
                lower_f=float(r["lower_f"]) if r["lower_f"] is not None else None,
                upper_f=float(r["upper_f"]) if r["upper_f"] is not None else None,
                yes_ask=float(r["yes_ask"]) if r["yes_ask"] is not None else None,
                yes_bid=float(r["yes_bid"]) if r["yes_bid"] is not None else None,
                yes_ask_size=int(r["yes_ask_size"]) if r["yes_ask_size"] is not None else None,
                yes_bid_size=int(r["yes_bid_size"]) if r["yes_bid_size"] is not None else None,
                snapshot_ts=r["ts"],
                status=r["status"],
            )
    return out


def _event_groups(station_filter: list[str] | None) -> dict[tuple[str, str, str, object], list[dict]]:
    """Group open kalshi_market rows by (event_ticker, station, var, valid_date)."""
    sql = """
    SELECT ticker, event_ticker, station, var, valid_date, lower_f, upper_f
      FROM kalshi_market
     WHERE status IN ('open', 'active')
       AND valid_date >= CURRENT_DATE
       AND (%(stations)s::text[] IS NULL OR station = ANY(%(stations)s))
    """
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"stations": station_filter})
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for r in cur.fetchall():
            key = (r["event_ticker"], r["station"], r["var"], r["valid_date"])
            groups[key].append(dict(r))
    return groups


def _is_partition(buckets: list[BucketQuote]) -> bool:
    """Sanity: edges should connect end-to-end with no overlaps or gaps."""
    if not buckets:
        return False
    sorted_b = sorted(
        buckets,
        key=lambda b: (float("-inf") if b.lower_f is None else b.lower_f),
    )
    # First bucket must be open-ended low or sensibly bounded; subsequent must
    # touch the previous upper edge.
    prev_upper: float | None = None
    for i, b in enumerate(sorted_b):
        lo = b.lower_f
        hi = b.upper_f
        if i == 0:
            # Leading bucket can be open-ended (-inf) or have an explicit floor.
            prev_upper = hi
            continue
        if lo is None or prev_upper is None:
            return False
        if abs(float(lo) - float(prev_upper)) > 1e-6:
            return False
        prev_upper = hi
    return True


def scan(
    min_mispricing: float = 0.03,
    max_snapshot_age_min: int = 30,
    min_depth: int = 1,
    stations: list[str] | None = None,
    now_utc: datetime | None = None,
) -> list[EventScan]:
    """Run the smile scan against current snapshots."""
    now = now_utc or datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(minutes=max_snapshot_age_min)
    station_filter = stations or list(ACTIVE_TRADE_STATIONS)

    quotes = _latest_snapshots(station_filter)
    groups = _event_groups(station_filter)

    results: list[EventScan] = []
    for (event_ticker, station, var, valid_date), markets in groups.items():
        bucket_quotes: list[BucketQuote] = []
        complete = 0
        for m in markets:
            q = quotes.get(m["ticker"])
            if q is None:
                q = BucketQuote(
                    ticker=m["ticker"],
                    lower_f=float(m["lower_f"]) if m["lower_f"] is not None else None,
                    upper_f=float(m["upper_f"]) if m["upper_f"] is not None else None,
                    yes_ask=None, yes_bid=None,
                    yes_ask_size=None, yes_bid_size=None,
                    snapshot_ts=None, status=None,
                )
            if q.yes_mid is not None and q.snapshot_ts is not None and q.snapshot_ts >= cutoff:
                complete += 1
            bucket_quotes.append(q)

        if len(bucket_quotes) < 2:
            continue
        if not _is_partition(bucket_quotes):
            log.debug("Skipping non-partition event %s (%d buckets)", event_ticker, len(bucket_quotes))
            continue

        mids = [q.yes_mid for q in bucket_quotes]
        if any(m is None for m in mids):
            # We need every bucket priced to compute over_round.
            continue
        over_round = sum(mids)  # type: ignore[arg-type]
        if over_round <= 0:
            continue

        scored: list[tuple[BucketQuote, float, float]] = []
        for q, mid in zip(bucket_quotes, mids):
            normalized = float(mid) / over_round  # type: ignore[arg-type]
            mispricing = normalized - float(mid)  # type: ignore[arg-type]
            scored.append((q, normalized, mispricing))

        results.append(EventScan(
            event_ticker=event_ticker,
            station=station,
            var=var,
            valid_date=valid_date,
            over_round=over_round,
            bucket_count=len(bucket_quotes),
            quotes_complete=complete,
            buckets=scored,
        ))

    # Filter to events with at least one bucket whose mispricing crosses the
    # threshold AND has minimum tradable depth on the relevant side.
    flagged: list[EventScan] = []
    for ev in results:
        for q, _, mispricing in ev.buckets:
            if abs(mispricing) < min_mispricing:
                continue
            depth = q.yes_ask_size if mispricing > 0 else q.yes_bid_size
            # min_depth=0 means "don't require depth info" — useful when
            # market_snapshot rows have null sizes (older capture path).
            if min_depth > 0 and (depth is None or depth < min_depth):
                continue
            flagged.append(ev)
            break
    flagged.sort(key=lambda e: e.max_abs_mispricing, reverse=True)
    return flagged


def _format_md(events: list[EventScan], min_mispricing: float, max_snapshot_age_min: int, now: datetime) -> str:
    lines: list[str] = []
    lines.append(f"# Smile Arbitrage Scan — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append(
        f"Scanned active/open events for active trade stations "
        f"(`{', '.join(ACTIVE_TRADE_STATIONS)}`). Flagged events have at least one "
        f"bucket whose normalized-share residual exceeds `{min_mispricing:.2f}` "
        f"with snapshot age ≤ `{max_snapshot_age_min}` min and non-zero depth on "
        f"the actionable side."
    )
    lines.append("")
    lines.append(
        "**Method.** `over_round = Σ_i yes_mid_i`. The buckets in an event "
        "partition the outcome, so the true sum must be `1.0`. Per-bucket "
        "`normalized_share - yes_mid` measures algebraic mispricing — positive "
        "means YES is too cheap (renormalized), negative means YES is too rich. "
        "Edge is *independent of forecast skill* but settlement risk and "
        "execution risk (fees, depth, snapshot staleness) still apply."
    )
    lines.append("")
    if not events:
        lines.append("_No events flagged._")
        return "\n".join(lines)

    for ev in events:
        lines.append(f"## {ev.event_ticker} — {ev.station} {ev.var} {ev.valid_date}")
        lines.append("")
        lines.append(
            f"over_round = **{ev.over_round:.3f}** "
            f"({'overpaying YES' if ev.over_round > 1 else 'overpaying NO'}); "
            f"buckets = {ev.bucket_count}; quotes_fresh = {ev.quotes_complete}"
        )
        lines.append("")
        lines.append("| Bucket | yes_mid | norm_share | mispricing | yes_ask×size | yes_bid×size | snap_age |")
        lines.append("|---|---:|---:|---:|---|---|---:|")
        for q, norm, mis in sorted(ev.buckets, key=lambda x: abs(x[2]), reverse=True):
            age_min = ((now - q.snapshot_ts).total_seconds() / 60.0) if q.snapshot_ts else None
            age_s = f"{age_min:.1f}m" if age_min is not None else "—"
            ask = f"{q.yes_ask:.2f}×{q.yes_ask_size}" if q.yes_ask is not None else "—"
            bid = f"{q.yes_bid:.2f}×{q.yes_bid_size}" if q.yes_bid is not None else "—"
            mid = q.yes_mid
            mid_s = f"{mid:.3f}" if mid is not None else "—"
            star = "**" if abs(mis) >= 0.03 else ""
            lines.append(
                f"| `{q.label}` | {mid_s} | {norm:.3f} | {star}{mis:+.3f}{star} | {ask} | {bid} | {age_s} |"
            )
        # Suggested trade summary: top mispriced bucket with executable side.
        top = max(ev.buckets, key=lambda x: abs(x[2]))
        q, norm, mis = top
        side = "YES" if mis > 0 else "NO"
        # Cost basis if you crossed now.
        if side == "YES" and q.yes_ask is not None:
            entry = float(q.yes_ask)
            fee = fee_for_order(entry, 100)  # nominal 100c order to read fee_load
            fee_load = (fee / 100.0) / entry if entry > 0 else 0.0
            lines.append("")
            lines.append(
                f"Suggested: **BUY YES** `{q.label}` at `{entry:.2f}` "
                f"(mispricing `{mis:+.3f}`, fee_load on 100 contracts ≈ `{fee_load:.2f}`)."
            )
        elif side == "NO" and q.yes_bid is not None:
            no_ask = 1.0 - float(q.yes_bid)
            fee = fee_for_order(no_ask, 100)
            fee_load = (fee / 100.0) / no_ask if no_ask > 0 else 0.0
            lines.append("")
            lines.append(
                f"Suggested: **BUY NO** `{q.label}` at `{no_ask:.2f}` "
                f"(mispricing `{mis:+.3f}`, fee_load on 100 contracts ≈ `{fee_load:.2f}`)."
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-bucket smile arbitrage scanner.")
    parser.add_argument("--min-mispricing", type=float, default=0.03,
                        help="Minimum |normalized_share - yes_mid| to flag (default 0.03).")
    parser.add_argument("--max-snapshot-age-min", type=int, default=30,
                        help="Reject quotes older than this many minutes (default 30).")
    parser.add_argument("--min-depth", type=int, default=1,
                        help="Minimum top-of-book size on the actionable side (default 1).")
    parser.add_argument("--stations", type=str, default=None,
                        help="Comma-separated station filter; defaults to ACTIVE_TRADE_STATIONS.")
    parser.add_argument("--output", type=str, default=None,
                        help="Write markdown report to this path (default research/reports/smile_arbitrage_<date>.md).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    stations = [s.strip().upper() for s in args.stations.split(",")] if args.stations else None
    now = datetime.now(tz=timezone.utc)
    events = scan(
        min_mispricing=args.min_mispricing,
        max_snapshot_age_min=args.max_snapshot_age_min,
        min_depth=args.min_depth,
        stations=stations,
        now_utc=now,
    )

    md = _format_md(events, args.min_mispricing, args.max_snapshot_age_min, now)
    out_path = Path(args.output) if args.output else REPORTS_DIR / f"smile_arbitrage_{now.strftime('%Y-%m-%d')}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    log.info("Wrote %d flagged events to %s", len(events), out_path)


if __name__ == "__main__":
    main()
