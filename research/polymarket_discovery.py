"""Read-only Polymarket weather-market discovery.

Find active Polymarket events that look weather/climate/temperature related,
then fetch public CLOB top-of-book for their outcome tokens. No auth, no
trading, no persistence.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
GAMMA_EVENT_SLUG_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"

WEATHER_KEYWORDS = [
    "weather",
    "temperature",
    "temperatures",
    "temp",
    "degree",
    "degrees",
    "rain",
    "rainfall",
    "snow",
    "snowfall",
    "hurricane",
    "storm",
    "tornado",
    "heat wave",
    "cold snap",
    "wildfire",
    "drought",
    "flood",
    "noaa",
]

BOT_GEO_KEYWORDS = [
    "new york",
    "nyc",
    "central park",
    "laguardia",
    "chicago",
    "midway",
    "miami",
    "atlanta",
    "denver",
    "los angeles",
    "philadelphia",
    "austin",
]


@dataclass(frozen=True)
class CandidateRow:
    event_id: str
    event_slug: str
    event_title: str
    market_id: str
    market_slug: str
    question: str
    end_date: str | None
    volume: float | None
    volume_24h: float | None
    liquidity: float | None
    outcomes: str
    resolution_source: str
    token_count: int
    best_yes_bid: float | None
    best_yes_ask: float | None
    best_yes_ask_size: float | None
    best_no_bid: float | None
    best_no_ask: float | None
    best_no_ask_size: float | None
    spread_yes: float | None
    matched_keywords: str
    matched_bot_geo: str
    likely_kalshi_overlap: str
    url: str


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _text_blob(event: dict, market: dict | None = None) -> str:
    parts = [
        event.get("title"),
        event.get("ticker"),
        event.get("slug"),
        event.get("description"),
        event.get("resolutionSource"),
    ]
    for tag in event.get("tags") or []:
        if isinstance(tag, dict):
            parts.extend([tag.get("label"), tag.get("slug")])
    if market:
        parts.extend([
            market.get("question"),
            market.get("slug"),
            market.get("description"),
            market.get("resolutionSource"),
            market.get("groupItemTitle"),
        ])
    return " ".join(str(p or "") for p in parts).lower()


def _surface_text_blob(event: dict, market: dict | None = None) -> str:
    """Short user-facing fields. Avoid long descriptions that create noisy hits."""
    parts = [
        event.get("title"),
        event.get("ticker"),
        event.get("slug"),
    ]
    for tag in event.get("tags") or []:
        if isinstance(tag, dict):
            parts.extend([tag.get("label"), tag.get("slug")])
    if market:
        parts.extend([
            market.get("question"),
            market.get("slug"),
            market.get("groupItemTitle"),
        ])
    return " ".join(str(p or "") for p in parts).lower()


def _matches(words: list[str], text: str) -> list[str]:
    hits = []
    for word in words:
        pattern = r"\b" + re.escape(word.lower()) + r"\b"
        if re.search(pattern, text):
            hits.append(word)
    return hits


def _fetch_events(max_pages: int, limit: int, timeout: int) -> list[dict]:
    events: list[dict] = []
    session = requests.Session()
    for page in range(max_pages):
        r = session.get(
            GAMMA_EVENTS_URL,
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": page * limit,
                "order": "volume_24hr",
                "ascending": "false",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        events.extend(batch)
        if len(batch) < limit:
            break
    return events


def _fetch_event_slugs(slugs: list[str], timeout: int) -> list[dict]:
    out = []
    session = requests.Session()
    for slug in slugs:
        r = session.get(GAMMA_EVENT_SLUG_URL.format(slug=slug), timeout=timeout)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        out.append(r.json())
    return out


def _book_top(token_id: str, timeout: int) -> tuple[float | None, float | None, float | None]:
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


def _likely_overlap(question: str, weather_hits: list[str], geo_hits: list[str]) -> str:
    q = question.lower()
    if geo_hits and any(w in q for w in ["temperature", "temp", "degree", "degrees", "weather"]):
        if any(w in q for w in ["today", "tomorrow", "daily", "high", "low"]):
            return "possible"
        if "highest temperature" in q:
            return "possible"
        return "weak"
    if geo_hits:
        return "weak"
    if weather_hits:
        return "none"
    return "none"


def discover(max_pages: int = 20, limit: int = 100, timeout: int = 20, fetch_books: bool = True) -> list[CandidateRow]:
    events = _fetch_events(max_pages=max_pages, limit=limit, timeout=timeout)
    rows: list[CandidateRow] = []
    seen_markets: set[str] = set()
    for event in events:
        event_text = _surface_text_blob(event)
        event_weather = _matches(WEATHER_KEYWORDS, event_text)
        event_geo = _matches(BOT_GEO_KEYWORDS, event_text)
        for market in event.get("markets") or []:
            market_id = str(market.get("id") or "")
            if not market_id or market_id in seen_markets:
                continue
            seen_markets.add(market_id)
            text = _surface_text_blob(event, market)
            weather_hits = sorted(set(event_weather + _matches(WEATHER_KEYWORDS, text)))
            geo_hits = sorted(set(event_geo + _matches(BOT_GEO_KEYWORDS, text)))
            if not weather_hits:
                continue

            tokens = [str(t) for t in _json_list(market.get("clobTokenIds"))]
            outcomes = [str(o) for o in _json_list(market.get("outcomes"))]
            yes_bid = yes_ask = yes_ask_size = None
            no_bid = no_ask = no_ask_size = None
            if fetch_books and tokens:
                try:
                    yes_bid, yes_ask, yes_ask_size = _book_top(tokens[0], timeout=timeout)
                except Exception:
                    pass
                if len(tokens) > 1:
                    try:
                        no_bid, no_ask, no_ask_size = _book_top(tokens[1], timeout=timeout)
                    except Exception:
                        pass

            question = str(market.get("question") or event.get("title") or "")
            rows.append(
                CandidateRow(
                    event_id=str(event.get("id") or ""),
                    event_slug=str(event.get("slug") or ""),
                    event_title=str(event.get("title") or ""),
                    market_id=market_id,
                    market_slug=str(market.get("slug") or ""),
                    question=question,
                    end_date=market.get("endDate") or event.get("endDate"),
                    volume=_float_or_none(market.get("volumeNum") or market.get("volume")),
                    volume_24h=_float_or_none(market.get("volume24hr") or market.get("volume24hrClob")),
                    liquidity=_float_or_none(market.get("liquidityNum") or market.get("liquidity")),
                    outcomes=", ".join(outcomes),
                    resolution_source=str(market.get("resolutionSource") or event.get("resolutionSource") or ""),
                    token_count=len(tokens),
                    best_yes_bid=yes_bid,
                    best_yes_ask=yes_ask,
                    best_yes_ask_size=yes_ask_size,
                    best_no_bid=no_bid,
                    best_no_ask=no_ask,
                    best_no_ask_size=no_ask_size,
                    spread_yes=(yes_ask - yes_bid) if yes_ask is not None and yes_bid is not None else None,
                    matched_keywords=", ".join(weather_hits),
                    matched_bot_geo=", ".join(geo_hits),
                    likely_kalshi_overlap=_likely_overlap(question, weather_hits, geo_hits),
                    url=f"https://polymarket.com/event/{event.get('slug')}",
                )
            )
    rows.sort(key=lambda r: (r.likely_kalshi_overlap != "possible", -(r.volume_24h or 0), -(r.volume or 0)))
    return rows


def write_csv(rows: list[CandidateRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _fmt_price(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _fmt_money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.0f}"


def render_markdown(rows: list[CandidateRow], scanned_events: int, max_pages: int, limit: int) -> str:
    possible = [r for r in rows if r.likely_kalshi_overlap == "possible"]
    weak = [r for r in rows if r.likely_kalshi_overlap == "weak"]
    none = [r for r in rows if r.likely_kalshi_overlap == "none"]
    lines = [
        f"# Polymarket Weather Discovery - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Scanned up to {max_pages * limit} active events from Gamma; fetched {scanned_events} events and found {len(rows)} weather/geography candidates.",
        "",
        "## Summary",
        "",
        "| category | count |",
        "|---|---:|",
        f"| possible Kalshi weather overlap | {len(possible)} |",
        f"| weak geography/weather overlap | {len(weak)} |",
        f"| weather/climate but not station daily-temp overlap | {len(none)} |",
        "",
        "## Best Candidates",
        "",
        "| overlap | question | end | 24h vol | YES bid | YES ask | spread | keywords | geo |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in rows[:30]:
        q = row.question.replace("|", "/")
        if len(q) > 90:
            q = q[:87] + "..."
        lines.append(
            f"| {row.likely_kalshi_overlap} | [{q}]({row.url}) | {row.end_date or '-'} | "
            f"{_fmt_money(row.volume_24h)} | {_fmt_price(row.best_yes_bid)} | {_fmt_price(row.best_yes_ask)} | "
            f"{_fmt_price(row.spread_yes)} | {row.matched_keywords or '-'} | {row.matched_bot_geo or '-'} |"
        )
    lines.extend([
        "",
        "## Read",
        "",
        "- `possible` means the market mentions one of our cities/stations plus temperature/weather language; manual mapping is still required.",
        "- `weak` means geography matched but the market is not a clean daily temperature bucket.",
        "- `none` means it is weather/climate related but not obviously comparable to Kalshi daily high buckets.",
        "- A clean cross-platform gap backtest needs repeated snapshots, not a one-time discovery pass.",
    ])
    return "\n".join(lines) + "\n"


def run(
    max_pages: int = 20,
    limit: int = 100,
    out_dir: Path = Path("research/reports"),
    slugs: list[str] | None = None,
) -> dict:
    events = _fetch_events(max_pages=max_pages, limit=limit, timeout=20)
    by_id = {str(e.get("id")): e for e in events}
    for event in _fetch_event_slugs(slugs or [], timeout=20):
        by_id[str(event.get("id"))] = event
    events = list(by_id.values())
    rows = []
    # Avoid fetching the large event payload twice by using a small local shim.
    original_fetch = globals()["_fetch_events"]
    try:
        globals()["_fetch_events"] = lambda max_pages, limit, timeout: events
        rows = discover(max_pages=max_pages, limit=limit, timeout=20, fetch_books=True)
    finally:
        globals()["_fetch_events"] = original_fetch
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"polymarket_weather_discovery_{date.today()}"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"
    write_csv(rows, csv_path)
    md_path.write_text(render_markdown(rows, len(events), max_pages, limit))
    return {"rows": len(rows), "events": len(events), "csv_path": str(csv_path), "report_path": str(md_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = run(max_pages=args.max_pages, limit=args.limit, out_dir=args.out_dir)
    print(Path(result["report_path"]).read_text())
