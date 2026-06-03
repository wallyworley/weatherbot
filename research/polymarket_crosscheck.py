"""Polymarket ↔ Kalshi temperature cross-check (research prototype).

For the cities the bot also trades, Polymarket runs the same daily high/low
temperature bracket markets. A successful trader is active there, so Polymarket
prices are an INDEPENDENT market reference. This script puts three numbers
side by side per bracket:

    Polymarket YES   |   Kalshi market (our ask)   |   our model fair

and flags where our model disagrees with BOTH venues — per the winner's-curse
finding, when two independent markets agree against us, the model is usually
the outlier, not the edge.

Research only. Does not trade. Usage:
    python -m weather_bot.research.polymarket_crosscheck            # all overlap cities, today
    python -m weather_bot.research.polymarket_crosscheck --station KNYC
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import date, datetime

from weather_bot.data import persistence

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_GAMMA = "https://gamma-api.polymarket.com"

# VERIFIED settlement-station mapping (read from Polymarket resolution rules
# 2026-06-03 — do NOT guess these; the city name is not the station).
#
# Two independent caveats make most cities non-comparable:
#  1. STATION: Polymarket frequently settles a DIFFERENT physical station than
#     Kalshi/our bot. e.g. PM "NYC" = LaGuardia (we trade Central Park);
#     PM "Chicago" = O'Hare (we trade Midway); PM "Denver" = Buckley SFB
#     (we trade Denver Intl). A cross-check is only meaningful where the
#     stations MATCH.
#  2. SOURCE: even at a matching station, Polymarket settles on Weather
#     Underground's raw "highest temperature recorded" while Kalshi/our bot
#     settle on the next-day NWS CLI (QC'd). These differ — historically the
#     raw obs runs cooler than CLI by up to ~0.5-1°F — so small probability
#     gaps are source basis, not edge. Only LARGE disagreements are signal.
#
#   our station -> (pm_city_in_title, pm_settlement_station, comparable)
PM_CITY: dict[str, tuple[str, str, bool]] = {
    "KMIA": ("Miami", "Miami Intl (KMIA) — same station", True),
    "KATL": ("Atlanta", "Hartsfield-Jackson (KATL) — same station", True),
    # NOT comparable — Polymarket settles a different station than we trade:
    "KNYC": ("NYC", "LaGuardia (KLGA); we trade Central Park", False),
    "KMDW": ("Chicago", "O'Hare (KORD); we trade Midway", False),
    "KDEN": ("Denver", "Buckley SFB; we trade Denver Intl", False),
    # Unverified — read each market's rules before enabling (do not guess):
    #   KDFW, KAUS, KSEA, KPHX, KBOS, KLAX, KHOU
}


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _bracket_bounds(label: str) -> tuple[float | None, float | None]:
    """'80-81°F' -> (80, 82); '67°F or below' -> (None, 68); '86°F or higher' -> (86, None).

    Upper bound is exclusive (hi+1) to match our kalshi_market convention.
    """
    label = label.replace("°F", "").replace("°", "").strip()
    m = re.match(r"(\d+)\s*-\s*(\d+)", label)
    if m:
        return float(m.group(1)), float(m.group(2)) + 1
    m = re.match(r"(\d+)\s*or below", label, re.I)
    if m:
        return None, float(m.group(1)) + 1
    m = re.match(r"(\d+)\s*or higher", label, re.I)
    if m:
        return float(m.group(1)), None
    m = re.match(r"(\d+)", label)
    if m:                       # single value "82" -> [82, 83)
        return float(m.group(1)), float(m.group(1)) + 1
    return None, None


def fetch_pm_brackets(city: str, target: date, var: str = "TMAX") -> list[dict]:
    """Return [{lower_f, upper_f, pm_yes, bid, ask, label}] for the PM event."""
    hl = "Highest" if var == "TMAX" else "Lowest"
    month = target.strftime("%B")
    q = f"{hl} temperature {city}".replace(" ", "%20")
    data = _get(f"{_GAMMA}/public-search?q={q}&limit_per_type=30")
    want = f"on {month} {target.day}"
    ev = None
    for e in data.get("events", []):
        title = e.get("title", "")
        if city.lower() in title.lower() and hl.lower() in title.lower() and want.lower() in title.lower():
            ev = e
            break
    if ev is None:
        return []
    out = []
    for m in ev.get("markets", []):
        lo, hi = _bracket_bounds(m.get("groupItemTitle", ""))
        try:
            yes = float(json.loads(m.get("outcomePrices", "[]"))[0])
        except Exception:
            yes = None
        out.append({"label": m.get("groupItemTitle", ""), "lower_f": lo, "upper_f": hi,
                    "pm_yes": yes, "bid": m.get("bestBid"), "ask": m.get("bestAsk")})
    return out


def fetch_our_book(station: str, target: date, var: str = "TMAX_DAILY") -> dict:
    """ticker-bucket -> {lower_f, upper_f, fair, mkt_ask, mkt_bid} from latest signal."""
    sql = """
        SELECT DISTINCT ON (km.ticker)
               km.ticker, km.lower_f, km.upper_f,
               s.fair_prob, s.market_ask, s.market_bid
          FROM kalshi_market km
          LEFT JOIN signal s ON s.ticker = km.ticker
         WHERE km.station = %s AND km.var = %s AND km.valid_date = %s
         ORDER BY km.ticker, s.ts DESC NULLS LAST
    """
    out = {}
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, var, target))
        for r in cur.fetchall():
            if r["lower_f"] is None and r["upper_f"] is None:
                continue
            out[float(r["lower_f"]) if r["lower_f"] is not None else None] = {
                "lower_f": r["lower_f"], "upper_f": r["upper_f"],
                "fair": float(r["fair_prob"]) if r["fair_prob"] is not None else None,
                "mkt_ask": float(r["market_ask"]) if r["market_ask"] is not None else None,
            }
    return out


def crosscheck(station: str, target: date, var: str = "TMAX", force: bool = False) -> None:
    entry = PM_CITY.get(station)
    if not entry:
        print(f"\n{station}: no verified Polymarket mapping — read the rules first, don't guess.")
        return
    city, pm_station, comparable = entry
    if not comparable and not force:
        print(f"\n=== {station} · SKIPPED — not comparable ===")
        print(f"  Polymarket '{city}' settles on {pm_station}. Different station → not a")
        print(f"  clean reference for our book. (Use --force to print anyway.)")
        return

    pm = fetch_pm_brackets(city, target, var)
    ours = fetch_our_book(station, target, var + "_DAILY")
    if not pm:
        print(f"\n=== {station} ({city}) {target} {var} — no live Polymarket event found ===")
        return

    print(f"\n=== {station} ({city}) · {target} · {var} ===")
    print(f"  PM settles: {pm_station}")
    print(f"  ⚠ source basis: Polymarket=Wunderground raw obs, us=NWS CLI — small gaps are basis, not edge.")
    print(f"  {'bracket':<15}{'PM yes':>8}{'Kalshi ask':>12}{'our fair':>10}   flag")
    # Threshold > one source-basis bracket of probability: only large, real gaps.
    GAP = 0.20
    for b in pm:
        if b["pm_yes"] is None:
            continue
        o = ours.get(b["lower_f"])
        fair = o["fair"] if o else None
        ask = o["mkt_ask"] if o else None
        pm_yes = b["pm_yes"]
        flag = ""
        if fair is not None and ask is not None:
            if fair - pm_yes > GAP and fair - ask > GAP:
                flag = "← model HIGH vs BOTH venues (suspect)"
            elif pm_yes - fair > GAP and ask - fair > GAP:
                flag = "← model LOW vs BOTH venues (suspect)"
            elif abs(pm_yes - ask) > GAP:
                flag = "← Kalshi/PM venue gap (maybe station/source)"
        fstr = f"{fair:.2f}" if fair is not None else "—"
        astr = f"{ask:.2f}" if ask is not None else "—"
        print(f"  {b['label']:<15}{pm_yes:>8.2f}{astr:>12}{fstr:>10}   {flag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", default=None, help="single station (e.g. KNYC)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default today)")
    ap.add_argument("--var", default="TMAX", choices=["TMAX", "TMIN"])
    ap.add_argument("--force", action="store_true", help="print even non-comparable cities")
    args = ap.parse_args()
    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    # Default to the VERIFIED-comparable cities only.
    if args.station:
        stations = [args.station]
    else:
        stations = [s for s, (_, _, ok) in PM_CITY.items() if ok]
    for st in stations:
        try:
            crosscheck(st, target, args.var, force=args.force)
        except Exception as exc:
            print(f"{st}: cross-check failed: {exc}")


if __name__ == "__main__":
    main()
