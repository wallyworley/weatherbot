"""
Minimal Kalshi REST client.

Kalshi uses RSA-signed requests: sign `timestamp + method + path` with your
private key, send base64 signature in headers.

Scope: read-only for MVP (markets + orderbook). Order placement goes through
a separate, deliberately gated module once paper-trading validates strategy.
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Iterable

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from weather_bot.config import KALSHI_API_KEY_ID, KALSHI_BASE_URL, KALSHI_PRIVATE_KEY_PATH

log = logging.getLogger(__name__)


class KalshiClient:
    def __init__(
        self,
        api_key_id: str | None = None,
        private_key_path: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key_id = api_key_id or KALSHI_API_KEY_ID
        self.base_url = (base_url or KALSHI_BASE_URL).rstrip("/")
        path = private_key_path or KALSHI_PRIVATE_KEY_PATH
        self._pk = None
        if path and Path(path).exists():
            with open(path, "rb") as f:
                self._pk = serialization.load_pem_private_key(f.read(), password=None)

    def _sign(self, ts_ms: str, method: str, path: str) -> str:
        if self._pk is None:
            raise RuntimeError("Kalshi private key not loaded")
        message = f"{ts_ms}{method}{path}".encode()
        sig = self._pk.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    def _headers(self, method: str, path: str) -> dict:
        ts_ms = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts_ms, method, path),
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            "Accept": "application/json",
        }

    def get(self, path: str, params: dict | None = None) -> dict:
        url = self.base_url + path
        headers = self._headers("GET", path)
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

    # ----- High-level helpers -----
    def list_events(self, series_ticker: str, status: str = "open", cursor: str | None = None) -> dict:
        return self.get("/events", params={"series_ticker": series_ticker, "status": status, "cursor": cursor})

    def list_markets(self, event_ticker: str, cursor: str | None = None) -> dict:
        params = {"event_ticker": event_ticker}
        if cursor:
            params["cursor"] = cursor
        return self.get("/markets", params=params)

    def get_orderbook(self, ticker: str) -> dict:
        return self.get(f"/markets/{ticker}/orderbook")

    def get_market(self, ticker: str) -> dict:
        return self.get(f"/markets/{ticker}")


def iter_weather_markets(
    client: KalshiClient,
    series_tickers: Iterable[str],
    statuses: Iterable[str] = ("open",),
    delay: float = 0.0,
) -> list[dict]:
    """Pull markets for the given weather series, across the requested event statuses.

    Kalshi's `/events` endpoint takes a single `status` (open/settled/closed/etc),
    so we iterate one status at a time and concatenate. `delay` sleeps between
    each request to stay under rate limits during large backfills.
    """
    out: list[dict] = []
    for series in series_tickers:
        for status in statuses:
            cursor = None
            while True:
                resp = client.list_events(series, status=status, cursor=cursor)
                if delay:
                    time.sleep(delay)
                events = resp.get("events", [])
                for e in events:
                    event_ticker = e["event_ticker"]
                    mcursor = None
                    while True:
                        mresp = client.list_markets(event_ticker, cursor=mcursor)
                        if delay:
                            time.sleep(delay)
                        out.extend(mresp.get("markets", []))
                        mcursor = mresp.get("cursor")
                        if not mcursor:
                            break
                cursor = resp.get("cursor")
                if not cursor:
                    break
    return out
