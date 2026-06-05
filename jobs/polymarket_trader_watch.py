"""Polymarket trader monitor + strategy learner.

Tracks a successful daily-temperature trader on Polymarket (default: the
profile surfaced via synthesis.trade) and *learns from* their trades rather
than copying them. Each run:

  1. Pulls their public activity + positions from Polymarket's data API.
  2. Records new trades into research/pm_trader/trades.jsonl.
  3. Upserts positions into a persistent store (accumulates beyond the API's
     500-row window, so the learning set grows over time).
  4. Labels settled positions win/loss and regenerates STRATEGY_NOTES.md — a
     running study of WHAT MAKES THEM WIN: edge by entry-price, market type,
     TMAX vs TMIN, and city.
  5. Emails a digest of new trades + the current headline lessons.

Goal: understand the winning strategy, not mirror the book. Nothing here
places trades.

Run:
    python -m weather_bot.jobs.polymarket_trader_watch
    python -m weather_bot.jobs.polymarket_trader_watch --no-email
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from weather_bot.jobs import notify_email

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_DATA_API = "https://data-api.polymarket.com"

ADDRESS = os.getenv("PM_TRADER_ADDRESS", "0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11")
STORE = Path(__file__).resolve().parent.parent / "research" / "pm_trader"
POS_STORE = STORE / "positions_store.json"
SEEN_TRADES = STORE / "seen_trades.json"
TRADES_LOG = STORE / "trades.jsonl"
NOTES = STORE / "STRATEGY_NOTES.md"

# US cities the bot also trades (for overlap callouts).
_OUR_CITIES = {"New York", "Denver", "Atlanta", "Chicago", "Miami", "Dallas",
               "Seattle", "Austin", "Houston", "Los Angeles", "Phoenix",
               "Boston", "San Antonio"}


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def _get(path: str) -> list | dict | None:
    req = urllib.request.Request(f"{_DATA_API}{path}", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        log.warning("data-api fetch failed for %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Parse market title → structured fields
# ---------------------------------------------------------------------------
def parse_market(title: str) -> dict:
    """Extract {var, city, market_type} from a Polymarket temp-market title."""
    t = title or ""
    var = "TMAX" if re.search(r"highest temp", t, re.I) else (
        "TMIN" if re.search(r"lowest temp", t, re.I) else "OTHER")
    m = re.search(r"temperature in (.+?) be ", t, re.I)
    city = m.group(1).strip() if m else "?"
    # Normalize "New York City" -> "New York" for overlap matching.
    city_norm = city.replace("New York City", "New York")
    if re.search(r"\bbetween\b", t, re.I):
        mtype = "narrow"        # "between 50-51°"
    elif re.search(r"or (higher|above|below|lower)", t, re.I):
        mtype = "threshold"     # "74°F or higher"
    else:
        mtype = "exact"         # "be 16°C on ..."
    return {"var": var, "city": city_norm, "market_type": mtype}


def _is_weather(title: str) -> bool:
    return bool(re.search(r"temperature|°|degrees", title or "", re.I))


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------
def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def _trade_won(pos: dict) -> bool | None:
    """Trade-level outcome for an INTRADAY trader: did he book a profit on this
    market (realized P&L > 0)? Settlement outcome is the wrong lens — he buys
    cheap brackets and sells the intraday rally, so most 'winners' resolve NO
    at settlement (curPrice 0) yet are large realized gains. Returns None when
    the position has no realized P&L yet (untouched / still fully open)."""
    r = pos.get("realizedPnl")
    if r is None or r == 0:
        return None
    return r > 0


# ---------------------------------------------------------------------------
# Analysis — the "how does he win" study
# ---------------------------------------------------------------------------
def _price_bucket(p: float) -> str:
    if p < 0.10: return "0.00-0.10 (deep tail)"
    if p < 0.30: return "0.10-0.30 (cheap)"
    if p < 0.50: return "0.30-0.50 (under)"
    if p < 0.70: return "0.50-0.70 (over)"
    if p < 0.90: return "0.70-0.90 (favorite)"
    return "0.90-1.00 (near-cert)"


def _fmt_table(rows: list[tuple], headers: tuple) -> str:
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    line = "| " + " | ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    body = "\n".join(
        "| " + " | ".join(str(r[i]).ljust(widths[i]) for i in range(len(headers))) + " |"
        for r in rows)
    return f"{line}\n{sep}\n{body}"


def analyze(positions: list[dict]) -> tuple[str, dict]:
    """Build the STRATEGY_NOTES markdown + a small headline dict for email."""
    wx = [p for p in positions if _is_weather(p.get("title", ""))]
    for p in wx:
        p["_m"] = parse_market(p.get("title", ""))

    def agg(items):
        n = len(items)
        decided = [p for p in items if _trade_won(p) is not None]
        wins = sum(1 for p in decided if _trade_won(p))
        pnl = sum(p.get("realizedPnl", 0) or 0 for p in items)
        wr = (wins / len(decided) * 100) if decided else float("nan")
        return n, len(decided), wr, pnl

    # 1) by entry-price bucket — the "buy favorites" thesis
    by_price = defaultdict(list)
    for p in wx:
        by_price[_price_bucket(float(p.get("avgPrice") or 0))].append(p)
    price_rows = []
    order = ["0.00-0.10 (deep tail)", "0.10-0.30 (cheap)", "0.30-0.50 (under)",
             "0.50-0.70 (over)", "0.70-0.90 (favorite)", "0.90-1.00 (near-cert)"]
    for b in order:
        if b not in by_price:
            continue
        n, sn, wr, pnl = agg(by_price[b])
        price_rows.append((b, n, sn, f"{wr:.0f}%" if sn else "—", f"${pnl:,.0f}"))

    # 2) market type × outcome side
    by_type = defaultdict(list)
    for p in wx:
        by_type[(p["_m"]["market_type"], p.get("outcome", "?"))].append(p)
    type_rows = []
    for k in sorted(by_type, key=lambda k: -len(by_type[k])):
        n, sn, wr, pnl = agg(by_type[k])
        type_rows.append((f"{k[0]} / {k[1]}", n, sn, f"{wr:.0f}%" if sn else "—", f"${pnl:,.0f}"))

    # 3) TMAX vs TMIN
    by_var = defaultdict(list)
    for p in wx:
        by_var[p["_m"]["var"]].append(p)
    var_rows = []
    for v in ("TMAX", "TMIN", "OTHER"):
        if v not in by_var:
            continue
        n, sn, wr, pnl = agg(by_var[v])
        var_rows.append((v, n, sn, f"{wr:.0f}%" if sn else "—", f"${pnl:,.0f}"))

    # 4) city (our overlap first)
    by_city = defaultdict(list)
    for p in wx:
        by_city[p["_m"]["city"]].append(p)
    city_rows = []
    for c in sorted(by_city, key=lambda c: -sum(p.get("realizedPnl", 0) or 0 for p in by_city[c])):
        n, sn, wr, pnl = agg(by_city[c])
        ours = " ◀ ours" if c in _OUR_CITIES else ""
        city_rows.append((c[:18] + ours, n, sn, f"{wr:.0f}%" if sn else "—", f"${pnl:,.0f}"))

    tot_n, tot_sn, tot_wr, tot_pnl = agg(wx)
    hdr = ("bucket/key", "n", "decided", "win%", "realizedP&L")

    # Strategy decoded — quantify the buy-cheap / sell-the-rally pattern.
    import statistics
    realized_total = sum(p.get("realizedPnl", 0) or 0 for p in wx) or 1.0
    cheap = [p for p in wx if (p.get("avgPrice") or 0) < 0.10]
    cheap_pnl = sum(p.get("realizedPnl", 0) or 0 for p in cheap)
    winners = [p for p in wx if (p.get("realizedPnl") or 0) > 0]
    exited_worthless = sum(1 for p in winners
                           if p.get("curPrice") is not None and p["curPrice"] <= 0.02)
    med_entry = statistics.median([p.get("avgPrice") or 0 for p in wx]) if wx else 0.0
    cheap_share = cheap_pnl / realized_total * 100
    worthless_share = (exited_worthless / len(winners) * 100) if winners else 0.0
    decoded = (
        "## Strategy decoded (from activity: buy-only, exits via REDEEM/MERGE)\n\n"
        f"**Buy-only barbell**, held to resolution. Median entry ${med_entry:.2f}; "
        f"{len(cheap)} of {len(wx)} positions entered below 10¢, but a sizable share "
        f"are favorites (>50¢) too. He does **not sell** (0 SELL trades in the activity "
        f"feed) — he holds to settlement and exits via REDEEM (winning shares pay $1; "
        f"cheap winners 20-100x) and MERGE on the negative-risk bracket structure. "
        f"His edge is **SELECTION** (buying brackets underpriced vs true probability) + "
        f"heavy **diversification** across many city-days, held to resolution — NOT "
        f"trading in and out. NOTE: the exact per-position realizedPnl on negRisk "
        f"markets (positive P&L on curPrice≈0 positions) involves merge accounting not "
        f"fully derivable from this data — do not over-interpret. win% below = "
        f"exit-inclusive realized P&L > 0.\n\n"
    )

    md = (
        f"# Polymarket trader study — {ADDRESS[:10]}…\n\n"
        f"_Regenerated {datetime.now(timezone.utc).isoformat(timespec='minutes')} · "
        f"learning set: {len(wx)} weather positions accumulated_\n\n"
        f"**Overall weather book:** {tot_n} positions · {tot_sn} decided · "
        f"{tot_wr:.0f}% win · realized **${tot_pnl:,.0f}**\n\n"
        f"{decoded}"
        f"## Edge by entry price\n\n{_fmt_table(price_rows, hdr)}\n\n"
        f"## By market structure × side\n\n{_fmt_table(type_rows, hdr)}\n\n"
        f"## TMAX vs TMIN\n\n{_fmt_table(var_rows, hdr)}\n\n"
        f"## By city (◀ = the bot also trades it)\n\n{_fmt_table(city_rows[:20], hdr)}\n"
    )
    headline = {"positions": tot_n, "settled": tot_sn, "win": tot_wr, "pnl": tot_pnl,
                "price_rows": price_rows}
    return md, headline


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run(send_email: bool = True) -> dict:
    STORE.mkdir(parents=True, exist_ok=True)
    first_run = not SEEN_TRADES.exists()   # seed-only: don't email the backlog
    activity = _get(f"/activity?user={ADDRESS}&limit=500") or []
    positions = _get(f"/positions?user={ADDRESS}&limit=500") or []
    value = _get(f"/value?user={ADDRESS}") or []
    portfolio = float(value[0]["value"]) if value else None

    # --- upsert positions into the persistent store (grows past 500-window) ---
    store = _load_json(POS_STORE, {})
    for p in positions:
        if p.get("asset"):
            store[p["asset"]] = p
    POS_STORE.write_text(json.dumps(store, separators=(",", ":")))

    # --- detect new trades ---
    seen = set(_load_json(SEEN_TRADES, []))
    new_trades = []
    for a in activity:
        if a.get("type") != "TRADE":
            continue
        key = f"{a.get('transactionHash')}|{a.get('asset')}|{a.get('side')}"
        if key in seen or not _is_weather(a.get("title", "")):
            seen.add(key)
            continue
        seen.add(key)
        m = parse_market(a.get("title", ""))
        rec = {
            "ts": (datetime.fromtimestamp(a["timestamp"], timezone.utc).isoformat()
                   if a.get("timestamp") else None),
            "side": a.get("side"), "outcome": a.get("outcome"),
            "price": a.get("price"), "usdc": a.get("usdcSize"), "size": a.get("size"),
            "city": m["city"], "var": m["var"], "market_type": m["market_type"],
            "title": a.get("title"), "slug": a.get("slug"), "tx": a.get("transactionHash"),
        }
        new_trades.append(rec)

    if new_trades:
        with TRADES_LOG.open("a") as f:
            for rec in new_trades:
                f.write(json.dumps(rec) + "\n")
    SEEN_TRADES.write_text(json.dumps(sorted(seen)))

    # --- regenerate the strategy study over the full accumulated store ---
    md, headline = analyze(list(store.values()))
    if portfolio is not None:
        md = md.replace("## Edge by entry price",
                        f"_Current portfolio value: ${portfolio:,.0f}_\n\n## Edge by entry price")
    NOTES.write_text(md)

    log.info("pm_trader: %d new trade(s), store=%d positions, portfolio=$%s",
             len(new_trades), len(store), f"{portfolio:,.0f}" if portfolio else "?")

    if send_email and new_trades and not first_run:
        _email_digest(new_trades, headline, portfolio)
    elif first_run:
        log.info("pm_trader: first run — seeded %d trades, email suppressed", len(new_trades))
    return {"new_trades": len(new_trades), "store": len(store), "portfolio": portfolio}


def _email_digest(new_trades: list[dict], headline: dict, portfolio: float | None) -> None:
    rows = "".join(
        f'<tr><td>{html_escape(t["ts"] or "")[:16]}</td>'
        f'<td><b>{html_escape(t["side"])}</b></td>'
        f'<td>{html_escape(t["city"])} {t["var"]}</td>'
        f'<td>{html_escape(t["market_type"])} [{html_escape(str(t["outcome"]))}]</td>'
        f'<td>@{float(t["price"] or 0):.2f}</td>'
        f'<td>${float(t["usdc"] or 0):,.0f}</td></tr>'
        for t in new_trades[:30]
    )
    learn = "".join(
        f"<tr><td>{html_escape(r[0])}</td><td>{r[1]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>"
        for r in headline.get("price_rows", [])
    )
    pv = f" · portfolio ${portfolio:,.0f}" if portfolio else ""
    body = (
        f"<p><b>{len(new_trades)} new trade(s)</b> from the Polymarket weather trader"
        f"{pv}.</p>"
        "<table border=1 cellpadding=4 cellspacing=0 style='border-collapse:collapse;font-size:13px'>"
        "<tr><th>when</th><th>side</th><th>market</th><th>type</th><th>price</th><th>size</th></tr>"
        f"{rows}</table>"
        "<p style='margin-top:14px'><b>What's working for him (edge by entry price):</b></p>"
        "<table border=1 cellpadding=4 cellspacing=0 style='border-collapse:collapse;font-size:13px'>"
        "<tr><th>price bucket</th><th>n</th><th>win%</th><th>realized P&L</th></tr>"
        f"{learn}</table>"
        "<p style='color:#888;font-size:12px'>Study, don't copy. Full notes: "
        "research/pm_trader/STRATEGY_NOTES.md on the VPS.</p>"
    )
    notify_email.send_email(
        f"[weatherbot] {len(new_trades)} new Polymarket trade(s) — weather trader", body)


def html_escape(s) -> str:
    import html
    return html.escape(str(s if s is not None else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(send_email=not args.no_email)


if __name__ == "__main__":
    main()
