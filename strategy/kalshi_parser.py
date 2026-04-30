"""
Kalshi weather market parser.

Kalshi daily temp contracts use event tickers like:
  KXHIGHNY-26APR18       (daily HIGH at NYC for April 18, 2026)
  KXLOWCHI-26APR18       (daily LOW at CHI for April 18, 2026)

Individual market tickers inside an event look like:
  KXHIGHNY-26APR18-T68    ("high exactly 68°F", a 1-degree bucket)
  KXHIGHNY-26APR18-B70    ("high >= 70°F", open upper)
  KXHIGHNY-26APR18-T67.5  (sometimes 0.5F buckets)

The ticker format has been stable but we don't rely on it — we parse the
`subtitle` / `yes_sub_title` field from the Kalshi market payload which
contains human-readable bucket text ("65-66°F", "69°F or above", etc.).

Station code mapping uses the event ticker prefix (NY, CHI, LA, MIA, DEN,
ATL, AUS, PHL).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Iterable

log = logging.getLogger(__name__)

STATION_BY_CODE = {
    "NY":  "KNYC",
    "CHI": "KORD",
    "LA":  "KLAX",
    "MIA": "KMIA",
    "DEN": "KDEN",
    "ATL": "KATL",
    "AUS": "KAUS",
    "PHL": "KPHL",
}

_EVENT_RE = re.compile(
    r"^KX(HIGH|LOW)(NY|CHI|LA|MIA|DEN|ATL|AUS|PHL)-(\d{2}[A-Z]{3}\d{2})$"
)


def parse_event_ticker(event_ticker: str) -> dict | None:
    m = _EVENT_RE.match(event_ticker)
    if not m:
        return None
    kind, loc, datestr = m.groups()
    try:
        d = datetime.strptime(datestr, "%y%b%d").date()
    except ValueError:
        return None
    return {
        "var": "TMAX_DAILY" if kind == "HIGH" else "TMIN_DAILY",
        "station": STATION_BY_CODE.get(loc),
        "valid_date": d,
    }


# Subtitle patterns we see on Kalshi weather markets.
# Handle both "54-55°" (legacy) and "54° to 55°" (current 2026) range forms,
# and both "X°F or above" and "X° or above" threshold forms.
_RANGE_RE    = re.compile(r"(\d+(?:\.\d+)?)\s*°?\s*F?\s*-\s*(\d+(?:\.\d+)?)\s*°?\s*F?", re.IGNORECASE)
_RANGE_TO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*°?\s*F?\s*to\s+(\d+(?:\.\d+)?)\s*°?\s*F?", re.IGNORECASE)
_ABOVE_RE    = re.compile(r"(\d+(?:\.\d+)?)\s*°?\s*F?\s*or\s*above", re.IGNORECASE)
_BELOW_RE    = re.compile(r"(\d+(?:\.\d+)?)\s*°?\s*F?\s*or\s*below", re.IGNORECASE)
_EXACT_RE    = re.compile(r"exactly\s+(\d+(?:\.\d+)?)\s*°?\s*F?", re.IGNORECASE)
_SINGLE_RE   = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*°?\s*F?\s*$", re.IGNORECASE)


def parse_bucket(subtitle: str) -> tuple[float | None, float | None] | None:
    """Parse market subtitle into (lower_f_inclusive, upper_f_exclusive)."""
    if not subtitle:
        return None
    s = subtitle.strip()

    for rx in (_RANGE_TO_RE, _RANGE_RE):
        m = rx.search(s)
        if m:
            lo = float(m.group(1))
            hi = float(m.group(2))
            # Kalshi buckets are inclusive-inclusive integer degrees;
            # we treat as [lo, hi+1) to avoid gaps.
            return (lo, hi + 1.0)

    m = _ABOVE_RE.search(s)
    if m:
        # "X° or above" → obs >= X (integer obs).
        return (float(m.group(1)), None)

    m = _BELOW_RE.search(s)
    if m:
        # "X° or below" → obs <= X → half-open upper = X + 1.
        return (None, float(m.group(1)) + 1.0)

    m = _EXACT_RE.search(s)
    if m:
        t = float(m.group(1))
        return (t, t + 1.0)

    m = _SINGLE_RE.match(s)
    if m:
        t = float(m.group(1))
        return (t, t + 1.0)

    return None


def parse_strikes(payload: dict) -> tuple[float | None, float | None] | None:
    """
    Prefer Kalshi's structured strike fields — they're unambiguous.

      strike_type='between'  → [floor_strike, cap_strike + 1)
      strike_type='greater'  → [floor_strike + 1, None)   (obs > floor; integer obs)
      strike_type='less'     → [None, cap_strike)         (obs < cap)
    """
    st = payload.get("strike_type")
    floor = payload.get("floor_strike")
    cap = payload.get("cap_strike")

    if st == "between" and floor is not None and cap is not None:
        return (float(floor), float(cap) + 1.0)
    if st == "greater" and floor is not None:
        return (float(floor) + 1.0, None)
    if st == "less" and cap is not None:
        return (None, float(cap))
    return None


def parse_market(payload: dict) -> dict | None:
    """Convert a Kalshi /markets item into a normalized row for our DB."""
    ticker = payload.get("ticker")
    event_ticker = payload.get("event_ticker")
    subtitle = payload.get("yes_sub_title") or payload.get("subtitle") or ""
    if not ticker or not event_ticker:
        return None

    evt = parse_event_ticker(event_ticker)
    if not evt:
        return None

    # 1) Prefer structured strike fields (most reliable).
    bucket = parse_strikes(payload)
    # 2) Fall back to subtitle regex for legacy / edge cases.
    if bucket is None:
        bucket = parse_bucket(subtitle)

    if bucket is None:
        log.warning(
            "Unparseable market %s: strike_type=%r floor=%r cap=%r subtitle=%r",
            ticker, payload.get("strike_type"),
            payload.get("floor_strike"), payload.get("cap_strike"), subtitle,
        )
        lower, upper = None, None
    else:
        lower, upper = bucket

    return dict(
        ticker=ticker,
        event_ticker=event_ticker,
        station=evt["station"],
        var=evt["var"],
        valid_date=evt["valid_date"],
        lower_f=lower,
        upper_f=upper,
        status=payload.get("status"),
        payload=payload,
    )


def parse_markets(markets: Iterable[dict]) -> list[dict]:
    out = []
    for m in markets:
        row = parse_market(m)
        if row:
            out.append(row)
    return out
