"""Read-only Polymarket market snapshot fetcher."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

import requests

from weather_bot.data import persistence

log = logging.getLogger(__name__)

GAMMA_EVENT_SLUG_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"

# Verified SAME-physical-station cities only (EXP-2026-011 amendment A2; rules
# citations in docs/research/EXP_2026_011_CROSS_VENUE_MAP_VERIFICATION.md).
# Slug city fragment -> settlement station (= our Kalshi station code for these).
# Excluded as non-comparable: nyc (KLGA), chicago (KORD), denver (Buckley),
# dallas (KDAL Love Field; we trade KDFW).
COMPARABLE_CITY_STATIONS = {
    "miami": "KMIA",
    "atlanta": "KATL",
    "austin": "KAUS",
    "seattle": "KSEA",
    "los-angeles": "KLAX",
    "houston": "KHOU",
    "san-francisco": "KSFO",
}


def default_event_slugs(today: date | None = None) -> list[str]:
    """Today's and tomorrow's daily-high events for the verified-comparable cities."""
    today = today or date.today()
    slugs = []
    for d in (today, today + timedelta(days=1)):
        for frag in COMPARABLE_CITY_STATIONS:
            slugs.append(
                f"highest-temperature-in-{frag}-on-{d.strftime('%B').lower()}-{d.day}-{d.year}"
            )
    return slugs


def _station_for_slug(slug: str) -> str | None:
    m = re.match(r"highest-temperature-in-(.+)-on-[a-z]+-\d{1,2}-\d{4}$", slug)
    return COMPARABLE_CITY_STATIONS.get(m.group(1)) if m else None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _book_top(token_id: str, timeout: int = 20) -> tuple[float | None, float | None, float | None]:
    r = requests.get(CLOB_BOOK_URL, params={"token_id": token_id}, timeout=timeout)
    r.raise_for_status()
    book = r.json()
    bids = [(float(x["price"]), float(x["size"])) for x in book.get("bids") or []]
    asks = [(float(x["price"]), float(x["size"])) for x in book.get("asks") or []]
    best_bid = max((p for p, _ in bids), default=None)
    ask_levels = sorted(asks, key=lambda x: x[0])
    best_ask = ask_levels[0][0] if ask_levels else None
    best_ask_size = ask_levels[0][1] if ask_levels else None
    return best_bid, best_ask, best_ask_size


def _parse_bucket(question: str) -> tuple[float | None, float | None]:
    q = question.replace("°", "")
    m = re.search(r"between\s+(-?\d+)\s*-\s*(-?\d+)\s*F", q, flags=re.IGNORECASE)
    if m:
        lo = float(m.group(1))
        # Polymarket wording is inclusive whole-degree buckets; represent as
        # half-open [lo, hi+1) to match our Kalshi bucket convention.
        hi_exclusive = float(m.group(2)) + 1.0
        return lo, hi_exclusive
    m = re.search(r"(-?\d+)\s*F\s+or\s+below", q, flags=re.IGNORECASE)
    if m:
        return None, float(m.group(1)) + 1.0
    m = re.search(r"(-?\d+)\s*F\s+or\s+higher", q, flags=re.IGNORECASE)
    if m:
        return float(m.group(1)), None
    return None, None


def _valid_date_from_slug(slug: str) -> date | None:
    m = re.search(r"on-([a-z]+)-(\d{1,2})-(\d{4})", slug)
    if not m:
        return None
    month_name, day, year = m.groups()
    month_lookup = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    month = month_lookup.get(month_name.lower())
    if not month:
        return None
    return date(int(year), month, int(day))


def fetch_event(slug: str, timeout: int = 20) -> dict:
    r = requests.get(GAMMA_EVENT_SLUG_URL.format(slug=slug), timeout=timeout)
    r.raise_for_status()
    return r.json()


def snapshot_event(slug: str, timeout: int = 20) -> list[dict]:
    event = fetch_event(slug, timeout=timeout)
    valid_date = _valid_date_from_slug(slug)
    station = _station_for_slug(slug)
    rows: list[dict] = []
    for market in event.get("markets") or []:
        tokens = [str(t) for t in _json_list(market.get("clobTokenIds"))]
        if not tokens:
            continue
        yes_bid = yes_ask = yes_ask_size = None
        no_bid = no_ask = no_ask_size = None
        try:
            yes_bid, yes_ask, yes_ask_size = _book_top(tokens[0], timeout=timeout)
        except Exception as exc:
            log.warning("Polymarket YES book failed for %s: %s", market.get("slug"), exc)
        if len(tokens) > 1:
            try:
                no_bid, no_ask, no_ask_size = _book_top(tokens[1], timeout=timeout)
            except Exception as exc:
                log.warning("Polymarket NO book failed for %s: %s", market.get("slug"), exc)
        question = str(market.get("question") or "")
        lower_f, upper_f = _parse_bucket(question)
        rows.append(
            {
                "venue": "POLYMARKET",
                "event_slug": slug,
                "market_slug": str(market.get("slug") or ""),
                "question": question,
                "station": station,
                "valid_date": valid_date,
                "lower_f": lower_f,
                "upper_f": upper_f,
                "resolution_source": str(market.get("resolutionSource") or event.get("resolutionSource") or ""),
                "yes_token_id": tokens[0] if tokens else None,
                "no_token_id": tokens[1] if len(tokens) > 1 else None,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "yes_ask_size": yes_ask_size,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "no_ask_size": no_ask_size,
                "volume_24h": _float_or_none(market.get("volume24hr") or market.get("volume24hrClob")),
                "liquidity": _float_or_none(market.get("liquidityNum") or market.get("liquidity")),
                "payload": {
                    "event_title": event.get("title"),
                    "event_volume_24h": event.get("volume24hr"),
                    "event_liquidity": event.get("liquidity"),
                    "outcomes": _json_list(market.get("outcomes")),
                    "outcomePrices": _json_list(market.get("outcomePrices")),
                },
            }
        )
    return rows


def run(slugs: list[str] | None = None) -> int:
    slugs = slugs or default_event_slugs()
    rows: list[dict] = []
    for slug in slugs:
        log.info("Polymarket snapshot: %s", slug)
        try:
            rows.extend(snapshot_event(slug))
        except Exception as exc:
            log.warning("Polymarket event failed for %s: %s", slug, exc)
    if rows:
        persistence.insert_external_market_snapshots(rows)
    log.info("Persisted %d Polymarket external snapshots", len(rows))
    return len(rows)
