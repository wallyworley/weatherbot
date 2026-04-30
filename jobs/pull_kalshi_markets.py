"""Cron job: pull open Kalshi weather markets into the DB.

Only fetches metadata + orderbook top-of-book; does not place orders.
"""
from __future__ import annotations

import argparse
import logging

from weather_bot.config import ACTIVE_STATIONS
from weather_bot.data import persistence
from weather_bot.strategy import kalshi_parser
from weather_bot.strategy.kalshi_client import KalshiClient, iter_weather_markets

SERIES_BY_STATION = {
    "KNYC": ["KXHIGHNY", "KXLOWNY"],
    "KORD": ["KXHIGHCHI", "KXLOWCHI"],
    "KLAX": ["KXHIGHLA", "KXLOWLA"],
    "KMIA": ["KXHIGHMIA", "KXLOWMIA"],
    "KDEN": ["KXHIGHDEN", "KXLOWDEN"],
    "KATL": ["KXHIGHATL", "KXLOWATL"],
    "KAUS": ["KXHIGHAUS", "KXLOWAUS"],
    "KPHL": ["KXHIGHPHL", "KXLOWPHL"],
}


def _snapshot_row(payload: dict) -> dict:
    def _dollars(key: str) -> float | None:
        v = payload.get(key)
        return float(v) if v is not None else None

    return dict(
        ticker=payload.get("ticker"),
        yes_ask=_dollars("yes_ask_dollars"),
        yes_bid=_dollars("yes_bid_dollars"),
        yes_ask_size=payload.get("yes_ask_size"),
        yes_bid_size=payload.get("yes_bid_size"),
        last_price=_dollars("last_price_dollars"),
        volume_24h=_dollars("volume_24h"),
        open_interest=_dollars("open_interest"),
    )


def run(statuses: tuple[str, ...] = ("open",), snapshot: bool = True, delay: float = 0.0):
    client = KalshiClient()
    series = []
    for code in ACTIVE_STATIONS:
        series.extend(SERIES_BY_STATION.get(code, []))
    raw = iter_weather_markets(client, series, statuses=statuses, delay=delay)
    rows = kalshi_parser.parse_markets(raw)
    if rows:
        persistence.upsert_kalshi_market(rows)
    logging.info("Upserted %d Kalshi market rows (statuses=%s)", len(rows), ",".join(statuses))

    # Snapshot top-of-book only for live runs — settled/closed books are stale
    # and pollute the backtest snapshot stream.
    if snapshot:
        snap_rows = [_snapshot_row(m) for m in raw if m.get("ticker")]
        if snap_rows:
            persistence.insert_market_snapshots(snap_rows)
        logging.info("Logged %d market snapshots", len(snap_rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--statuses",
        default="open",
        help="Comma-separated event statuses to pull (e.g. 'open' or 'settled,closed'). Default: open.",
    )
    ap.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Skip writing to market_snapshot. Use for backfills where the orderbook is stale.",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to sleep between Kalshi requests. Set ~0.3 for backfills to avoid 429s.",
    )
    args = ap.parse_args()
    run(
        statuses=tuple(s.strip() for s in args.statuses.split(",") if s.strip()),
        snapshot=not args.no_snapshot,
        delay=args.delay,
    )
