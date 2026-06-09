"""EXP-2026-011 (amendment A5) — research-only Kalshi WebSocket book collector.

Subscribes to `orderbook_delta` for active weather tickers and records high-cadence,
timestamped top-of-book into `kalshi_ws_book_event`, to tighten reprice-onset timing against
polling censoring. READ-ONLY market data: it never sends order commands and is never read by
production probabilities, sizing, execution, gates, or station activation.

Long-running daemon. Reconnects with backoff; refreshes the ticker set periodically.
Run: `python -m weather_bot.jobs.kalshi_ws_book_watch` (optionally `--once-seconds N` to
capture a bounded smoke and exit).
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
from weather_bot.strategy.kalshi_client import KalshiClient

log = logging.getLogger(__name__)

TICKER_REFRESH_SEC = 600          # re-pull active tickers + resubscribe every 10 min
FLUSH_EVERY = 25                  # batch DB writes
FLUSH_SECONDS = 5.0
BACKOFF_START = 2.0
BACKOFF_MAX = 60.0


class _Book:
    """Minimal per-ticker book reconstructed from snapshot + deltas, to derive top-of-book.
    Kalshi prices are integer cents; best yes bid = max yes buy price; best yes ask =
    100 - best no buy price (buying yes == selling no)."""

    def __init__(self) -> None:
        self.yes: dict[int, int] = {}
        self.no: dict[int, int] = {}

    def apply_snapshot(self, msg: dict) -> None:
        self.yes = {int(p): int(s) for p, s in (msg.get("yes") or []) if int(s) > 0}
        self.no = {int(p): int(s) for p, s in (msg.get("no") or []) if int(s) > 0}

    def apply_delta(self, msg: dict) -> None:
        side = self.yes if msg.get("side") == "yes" else self.no
        price = int(msg["price"])
        side[price] = side.get(price, 0) + int(msg["delta"])
        if side[price] <= 0:
            side.pop(price, None)

    def top_of_book(self) -> tuple[float | None, float | None]:
        yes_bid = float(max(self.yes)) if self.yes else None
        yes_ask = float(100 - max(self.no)) if self.no else None
        return yes_bid, yes_ask


def _exchange_ts(msg: dict) -> datetime | None:
    ts_ms = msg.get("ts_ms")
    if ts_ms is not None:
        return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
    ts = msg.get("ts")
    if ts is not None:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return None


async def _flush(buf: list[dict]) -> None:
    if not buf:
        return
    rows = list(buf)
    buf.clear()
    await asyncio.to_thread(persistence.insert_kalshi_ws_book_events, rows)


async def _run_once(client: KalshiClient, deadline: float | None) -> None:
    tickers = await asyncio.to_thread(persistence.active_weather_tickers)
    if not tickers:
        log.warning("no active weather tickers; sleeping")
        await asyncio.sleep(30)
        return
    log.info("subscribing orderbook_delta for %d tickers", len(tickers))
    books: dict[str, _Book] = {}
    last_top: dict[str, tuple[float | None, float | None]] = {}
    buf: list[dict] = []
    last_flush = asyncio.get_event_loop().time()
    last_refresh = asyncio.get_event_loop().time()

    async with websockets.connect(
        client.WS_URL, additional_headers=client.ws_auth_headers(), ping_interval=10, ping_timeout=10
    ) as ws:
        await ws.send(json.dumps({
            "id": 1, "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta"], "market_tickers": tickers},
        }))
        while True:
            now = asyncio.get_event_loop().time()
            if deadline is not None and now >= deadline:
                await _flush(buf)
                return
            if now - last_refresh >= TICKER_REFRESH_SEC:
                return  # bubble up to reconnect + resubscribe with a fresh ticker set
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=FLUSH_SECONDS)
            except asyncio.TimeoutError:
                if asyncio.get_event_loop().time() - last_flush >= FLUSH_SECONDS:
                    await _flush(buf)
                    last_flush = asyncio.get_event_loop().time()
                continue
            received_at = datetime.now(tz=timezone.utc)
            try:
                event = json.loads(raw)
            except Exception:
                continue
            mtype = event.get("type")
            if mtype not in ("orderbook_snapshot", "orderbook_delta"):
                continue
            msg = event.get("msg") or {}
            ticker = msg.get("market_ticker")
            if not ticker:
                continue
            book = books.setdefault(ticker, _Book())
            if mtype == "orderbook_snapshot":
                book.apply_snapshot(msg)
            else:
                try:
                    book.apply_delta(msg)
                except Exception:
                    pass
            yes_bid, yes_ask = book.top_of_book()
            # Volume control: only persist when the top-of-book actually changes (snapshots
            # always kept). This drops no-op deltas while preserving every center move the
            # onset-timing audit needs. Cuts ~27M rows/day to a small fraction.
            top = (yes_bid, yes_ask)
            if mtype == "orderbook_delta" and last_top.get(ticker) == top:
                continue
            last_top[ticker] = top
            buf.append({
                "ticker": ticker,
                "msg_type": mtype,
                "seq": event.get("seq"),
                "exchange_ts": _exchange_ts(msg) or _exchange_ts(event),
                "received_at": received_at,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "payload": msg,
            })
            if len(buf) >= FLUSH_EVERY:
                await _flush(buf)
                last_flush = asyncio.get_event_loop().time()


async def main_async(once_seconds: int | None) -> None:
    client = KalshiClient()
    if client._pk is None:
        raise SystemExit("Kalshi private key not loaded; cannot authenticate WS")
    deadline = (asyncio.get_event_loop().time() + once_seconds) if once_seconds else None
    backoff = BACKOFF_START
    while True:
        try:
            await _run_once(client, deadline)
            backoff = BACKOFF_START
        except Exception as exc:
            log.warning("ws session ended (%s); reconnecting in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
        if deadline is not None and asyncio.get_event_loop().time() >= deadline:
            return


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="EXP-2026-011 research-only Kalshi WS book collector.")
    ap.add_argument("--once-seconds", type=int, default=None,
                    help="capture for N seconds then exit (bounded smoke); default = run forever")
    args = ap.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async(args.once_seconds))


if __name__ == "__main__":
    main()
