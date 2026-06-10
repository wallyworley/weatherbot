"""EXP-2026-011 (amendment A7) — research-only Polymarket WebSocket book collector.

Subscribes to the public CLOB market channel for the verified-comparable cities' YES tokens
and records high-cadence, timestamped top-of-book into `polymarket_ws_book_event`, to tighten
the cross-venue channel's PM-side observation timing against polling censoring (the polled
path is ~minutes; t0 anchors on WeatherBot's genuine first sight). READ-ONLY market data: no
auth, no orders, never read by production probabilities, sizing, execution, gates, or station
activation.

Long-running daemon. Reconnects with backoff; refreshes the asset set every 10 minutes from
the polled snapshot universe (`persistence.polymarket_ws_assets`), so new days' markets are
picked up automatically once the poller has seen them.
Run: `python -m weather_bot.jobs.polymarket_ws_book_watch` (optionally `--once-seconds N`).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

import websockets

from weather_bot.data import persistence

log = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
ASSET_REFRESH_SEC = 600           # resubscribe with a fresh asset set every 10 min
PING_SEC = 10.0                   # Polymarket's server expects an application-level PING
FLUSH_EVERY = 25
FLUSH_SECONDS = 5.0
BACKOFF_START = 2.0
BACKOFF_MAX = 60.0


class _Book:
    """Per-asset book from `book` snapshots + `price_change` level updates (sizes are
    absolute replacements; size 0 removes the level). Prices are dollars (0..1)."""

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    @staticmethod
    def _levels(raw) -> dict[float, float]:
        out: dict[float, float] = {}
        for lvl in raw or []:
            try:
                p, s = float(lvl["price"]), float(lvl["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                out[p] = s
        return out

    def apply_snapshot(self, msg: dict) -> None:
        self.bids = self._levels(msg.get("bids") or msg.get("buys"))
        self.asks = self._levels(msg.get("asks") or msg.get("sells"))

    def apply_price_change(self, msg: dict) -> None:
        changes = msg.get("changes")
        if changes is None:
            changes = [msg]
        for ch in changes:
            try:
                price = float(ch["price"])
                size = float(ch["size"])
                side = str(ch.get("side", "")).upper()
            except (KeyError, TypeError, ValueError):
                continue
            book = self.bids if side == "BUY" else self.asks
            if size <= 0:
                book.pop(price, None)
            else:
                book[price] = size

    def top_of_book(self) -> tuple[float | None, float | None]:
        bid = max(self.bids) if self.bids else None
        ask = min(self.asks) if self.asks else None
        return bid, ask


def _exchange_ts(msg: dict) -> datetime | None:
    ts = msg.get("timestamp")
    if ts is None:
        return None
    try:
        ms = float(ts)
        if ms > 1e12:  # epoch milliseconds
            ms /= 1000.0
        return datetime.fromtimestamp(ms, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


async def _flush(buf: list[dict]) -> None:
    if not buf:
        return
    rows = list(buf)
    buf.clear()
    await asyncio.to_thread(persistence.insert_polymarket_ws_book_events, rows)


async def _run_once(deadline: float | None) -> None:
    assets = await asyncio.to_thread(persistence.polymarket_ws_assets)
    if not assets:
        log.warning("no Polymarket assets in the polled universe yet; sleeping")
        await asyncio.sleep(30)
        return
    meta = {a["asset_id"]: a for a in assets}
    asset_ids = list(meta)
    log.info("subscribing market channel for %d assets", len(asset_ids))
    books: dict[str, _Book] = {}
    last_top: dict[str, tuple[float | None, float | None]] = {}
    buf: list[dict] = []
    loop = asyncio.get_event_loop()
    last_flush = loop.time()
    last_refresh = loop.time()
    last_ping = loop.time()

    async with websockets.connect(WS_URL, ping_interval=10, ping_timeout=10) as ws:
        await ws.send(json.dumps({"type": "market", "assets_ids": asset_ids}))
        while True:
            now = loop.time()
            if deadline is not None and now >= deadline:
                await _flush(buf)
                return
            if now - last_refresh >= ASSET_REFRESH_SEC:
                await _flush(buf)
                return  # bubble up to reconnect + resubscribe with a fresh asset set
            if now - last_ping >= PING_SEC:
                with contextlib.suppress(Exception):
                    await ws.send("PING")
                last_ping = now
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=FLUSH_SECONDS)
            except asyncio.TimeoutError:
                if loop.time() - last_flush >= FLUSH_SECONDS:
                    await _flush(buf)
                    last_flush = loop.time()
                continue
            received_at = datetime.now(tz=timezone.utc)
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            if raw == "PONG":
                continue
            try:
                events = json.loads(raw)
            except Exception:
                continue
            if isinstance(events, dict):
                events = [events]
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                etype = event.get("event_type")
                asset_id = event.get("asset_id")
                if etype not in ("book", "price_change") or asset_id not in meta:
                    continue
                book = books.setdefault(asset_id, _Book())
                if etype == "book":
                    book.apply_snapshot(event)
                else:
                    book.apply_price_change(event)
                bid, ask = book.top_of_book()
                top = (bid, ask)
                # Volume control (same as A5): persist deltas only on top-of-book change.
                if etype == "price_change" and last_top.get(asset_id) == top:
                    continue
                last_top[asset_id] = top
                m = meta[asset_id]
                buf.append({
                    "asset_id": asset_id,
                    "market_slug": m["market_slug"],
                    "station": m["station"],
                    "valid_date": m["valid_date"],
                    "lower_f": m["lower_f"],
                    "upper_f": m["upper_f"],
                    "msg_type": etype,
                    "exchange_ts": _exchange_ts(event),
                    "received_at": received_at,
                    "bid": bid,
                    "ask": ask,
                    "payload": {k: event.get(k) for k in ("timestamp", "hash", "market")},
                })
            if len(buf) >= FLUSH_EVERY:
                await _flush(buf)
                last_flush = loop.time()


async def main_async(once_seconds: int | None) -> None:
    deadline = (asyncio.get_event_loop().time() + once_seconds) if once_seconds else None
    backoff = BACKOFF_START
    while True:
        try:
            await _run_once(deadline)
            backoff = BACKOFF_START
        except Exception as exc:
            log.warning("ws session ended (%s); reconnecting in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
        if deadline is not None and asyncio.get_event_loop().time() >= deadline:
            return


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="EXP-2026-011 research-only Polymarket WS book collector.")
    ap.add_argument("--once-seconds", type=int, default=None,
                    help="capture for N seconds then exit (bounded smoke); default = run forever")
    args = ap.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async(args.once_seconds))


if __name__ == "__main__":
    main()
