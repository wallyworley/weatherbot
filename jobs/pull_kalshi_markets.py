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
    # Series tickers verified against Kalshi API on 2026-05-24. Several earlier
    # entries were silently wrong (e.g. "KXHIGHLA" → actual "KXHIGHLAX"); since
    # those cities were not in ACTIVE_FETCH_STATIONS the bug was latent. Now
    # that the expansion enables them, the tickers must match exactly.
    # KXLOW markets retained for the original cities but appear to be dormant
    # outside winter — Kalshi serves them seasonally.
    "KNYC": ["KXHIGHNY", "KXLOWNY"],
    # Kalshi CHI markets settle on Chicago Midway (KMDW), not O'Hare (KORD).
    # Verified 2026-05-02 from market payload rules_primary text. KORD entry
    # retained for backward-compat in case ACTIVE_STATIONS ever adds it back.
    "KMDW": ["KXHIGHCHI", "KXLOWCHI"],
    "KORD": ["KXHIGHCHI", "KXLOWCHI"],
    "KMIA": ["KXHIGHMIA", "KXLOWMIA"],
    # 2026-05-24: fixed wrong tickers (KXHIGHLA→KXHIGHLAX, KXHIGHATL→KXHIGHTATL,
    # KXHIGHPHL→KXHIGHPHIL). KXLOW dropped for these — they had wrong tickers
    # too and likely don't exist for these cities; add back if/when verified.
    "KLAX": ["KXHIGHLAX"],
    "KATL": ["KXHIGHTATL"],
    "KAUS": ["KXHIGHAUS"],
    "KPHL": ["KXHIGHPHIL"],
    "KDEN": ["KXHIGHDEN"],
    # 2026-05-24 expansion: fetch-only daily-high cities.
    "KDCA": ["KXHIGHTDC"],
    "KBOS": ["KXHIGHTBOS"],
    "KPHX": ["KXHIGHTPHX"],
    "KDFW": ["KXHIGHTDAL"],
    "KSFO": ["KXHIGHTSFO"],
    "KSEA": ["KXHIGHTSEA"],
    "KLAS": ["KXHIGHTLV"],
    "KMSY": ["KXHIGHTNOLA"],
    "KMSP": ["KXHIGHTMIN"],
    "KSAT": ["KXHIGHTSATX"],
    "KOKC": ["KXHIGHTOKC"],
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
        # NO-side captured separately. Kalshi's payload may not always include
        # these fields (some endpoints only return YES side); default to None.
        no_ask=_dollars("no_ask_dollars"),
        no_bid=_dollars("no_bid_dollars"),
        no_ask_size=payload.get("no_ask_size"),
        no_bid_size=payload.get("no_bid_size"),
        status=payload.get("status"),
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
