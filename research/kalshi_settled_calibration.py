"""EXP-2026-015: venue-wide Kalshi settlement-calibration sweep.

RESEARCH-ONLY. No production change. Locked pre-registration:
docs/research/EXP_2026_015_VENUE_CALIBRATION_SWEEP.md (registry EXP-2026-015).

Three phases (run on the VPS):
    backfill  : census all settled markets in the trailing window into kalshi_settled_market
    candles   : fetch the settlement-eve daily candle (executable yes bid/ask) for liquid rows
    report    : the locked grid (category x price band x side), costs in, chronological halves,
                cluster bootstrap by event_ticker, candidate rule per prereg §7

Usage:
    python -m weather_bot.research.kalshi_settled_calibration backfill --days 90
    python -m weather_bot.research.kalshi_settled_calibration candles --limit 8000
    python -m weather_bot.research.kalshi_settled_calibration report --out report.md
"""
from __future__ import annotations

import argparse
import random
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from weather_bot.data import persistence
from weather_bot.research.market_longshot_bias import cluster_boot_ci, taker_fee
from weather_bot.strategy.kalshi_client import KalshiClient

THROTTLE_S = 0.25            # <= ~4 req/s, prereg §8 rate-limit citizenship
MIN_VOLUME = 500             # candle fetch only for liquid markets (prereg §3)
MIN_STORE_VOLUME = 100       # scale amendment 2026-06-10: census-by-count below this
CATEGORY_SAMPLE_CAP = 1500   # scale amendment: deterministic per-category candle sample
BANDS = [(0.0, 0.05), (0.05, 0.15), (0.15, 0.35), (0.35, 0.65),
         (0.65, 0.85), (0.85, 0.95), (0.95, 1.001)]
MIN_CELL_N = 50
MIN_EDGE = 0.01
SEED = 1337


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _ts(v):
    if not v:
        return None
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _series_categories(client: KalshiClient) -> dict[str, str]:
    out: dict[str, str] = {}
    cursor = None
    while True:
        params = {"limit": 2000}
        if cursor:
            params["cursor"] = cursor
        res = client.get("/series", params)
        for s in res.get("series", []):
            t = s.get("ticker")
            if t:
                out[t] = s.get("category") or "?"
        cursor = res.get("cursor")
        if not cursor:
            break
        time.sleep(THROTTLE_S)
    return out


def _category(series: str, ticker: str, cats: dict[str, str]) -> str:
    if ticker.startswith("KXMVE"):
        return "PARLAY"
    return cats.get(series, "?")


def backfill(days: int) -> None:
    client = KalshiClient()
    cats = _series_categories(client)
    print(f"series catalog: {len(cats)}")
    now = int(datetime.now(timezone.utc).timestamp())
    cursor = None
    n_rows = 0
    n_skipped_thin = 0
    sql = """
    INSERT INTO kalshi_settled_market
        (ticker, event_ticker, series_ticker, category, title, open_time, close_time,
         result, volume_fp, liquidity_dollars, last_price_dollars, strike_type, ref_status)
    VALUES (%(ticker)s, %(event_ticker)s, %(series_ticker)s, %(category)s, %(title)s,
            %(open_time)s, %(close_time)s, %(result)s, %(volume_fp)s, %(liquidity_dollars)s,
            %(last_price_dollars)s, %(strike_type)s, %(ref_status)s)
    ON CONFLICT (ticker) DO UPDATE SET
        result = EXCLUDED.result, volume_fp = EXCLUDED.volume_fp,
        ref_status = CASE WHEN kalshi_settled_market.ref_status IN ('ok','no_candle')
                          THEN kalshi_settled_market.ref_status ELSE EXCLUDED.ref_status END,
        updated_at = now()
    """
    while True:
        params = {"status": "settled", "limit": 1000,
                  "min_close_ts": now - days * 86400, "max_close_ts": now}
        if cursor:
            params["cursor"] = cursor
        res = client.get("/markets", params)
        mkts = res.get("markets", [])
        rows = []
        for m in mkts:
            ticker = m.get("ticker")
            if not ticker:
                continue
            # Scale amendment (2026-06-10): the settled universe is ~440k markets/day,
            # overwhelmingly zero-volume auto-generated parlay legs. Census them by count;
            # store rows only at >= MIN_STORE_VOLUME.
            if (_num(m.get("volume_fp")) or 0.0) < MIN_STORE_VOLUME:
                n_skipped_thin += 1
                continue
            series = ticker.split("-")[0]
            open_t, close_t = _ts(m.get("open_time")), _ts(m.get("close_time"))
            vol = _num(m.get("volume_fp")) or 0.0
            result = (m.get("result") or "").lower()
            if result not in ("yes", "no"):
                status = "no_result"
            elif vol < MIN_VOLUME:
                status = "low_volume"
            elif not open_t or not close_t or (close_t - open_t) < timedelta(days=1):
                status = "short_life"
            else:
                status = "pending"
            rows.append({
                "ticker": ticker, "event_ticker": m.get("event_ticker"),
                "series_ticker": series,
                "category": _category(series, ticker, cats),
                "title": (m.get("title") or "")[:300],
                "open_time": open_t, "close_time": close_t, "result": result,
                "volume_fp": vol, "liquidity_dollars": _num(m.get("liquidity_dollars")),
                "last_price_dollars": _num(m.get("last_price_dollars")),
                "strike_type": m.get("strike_type"), "ref_status": status,
            })
        if rows:
            with persistence.connect() as conn, conn.cursor() as cur:
                cur.executemany(sql, rows)
                conn.commit()
            n_rows += len(rows)
        cursor = res.get("cursor")
        if (n_rows + n_skipped_thin) % 50000 < 1000:
            print(f"  stored {n_rows} | census-skipped thin {n_skipped_thin}...", flush=True)
        if not cursor or not mkts:
            break
        time.sleep(THROTTLE_S)
    print(f"backfill complete: stored {n_rows}, census-skipped {n_skipped_thin} "
          f"(volume < {MIN_STORE_VOLUME}) over {days} days")


def fetch_candles(limit: int) -> None:
    client = KalshiClient()
    with persistence.connect() as conn, conn.cursor() as cur:
        # Scale amendment (2026-06-10): deterministic stratified sample — md5(ticker)
        # ordering caps each category at CATEGORY_SAMPLE_CAP candle fetches (unbiased,
        # reproducible). ~1,500/category is ample for 7 bands x 2 halves at n>=50.
        cur.execute(
            """
            SELECT ticker, series_ticker, close_time FROM (
                SELECT ticker, series_ticker, close_time, ref_status,
                       row_number() OVER (PARTITION BY category ORDER BY md5(ticker)) AS rk
                FROM kalshi_settled_market
                WHERE ref_status IN ('pending', 'ok', 'no_candle', 'err')
            ) s
            WHERE s.rk <= %(cap)s AND s.ref_status = 'pending'
            ORDER BY close_time LIMIT %(n)s
            """,
            {"n": limit, "cap": CATEGORY_SAMPLE_CAP},
        )
        todo = list(cur.fetchall())
    print(f"candle fetch: {len(todo)} pending")
    done = 0
    for r in todo:
        close_t = r["close_time"]
        ref_day = (close_t.astimezone(timezone.utc).date() - timedelta(days=1))
        day_start = datetime(ref_day.year, ref_day.month, ref_day.day, tzinfo=timezone.utc)
        start_ts = int(day_start.timestamp())
        end_ts = start_ts + 86400
        bid = ask = None
        status = "no_candle"
        try:
            cd = client.get(
                f"/series/{r['series_ticker']}/markets/{r['ticker']}/candlesticks",
                {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 1440},
            )
            candles = [k for k in cd.get("candlesticks", [])
                       if (k.get("end_period_ts") or 0) <= end_ts]
            if candles:
                k = candles[-1]
                bid = _num((k.get("yes_bid") or {}).get("close_dollars"))
                ask = _num((k.get("yes_ask") or {}).get("close_dollars"))
                if bid is not None and ask is not None and 0 < ask <= 1 and 0 <= bid <= ask:
                    status = "ok"
                else:
                    bid = ask = None
        except Exception:
            status = "err"
        with persistence.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE kalshi_settled_market
                   SET ref_day=%(d)s, ref_yes_bid=%(b)s, ref_yes_ask=%(a)s,
                       ref_status=%(st)s, updated_at=now() WHERE ticker=%(t)s""",
                {"d": ref_day, "b": bid, "a": ask, "st": status, "t": r["ticker"]},
            )
            conn.commit()
        done += 1
        if done % 500 == 0:
            print(f"  {done}/{len(todo)} candles fetched...")
        time.sleep(THROTTLE_S)
    print(f"candle fetch complete: {done}")


def _band(mid: float) -> str:
    for lo, hi in BANDS:
        if lo <= mid < hi:
            return f"{lo:.2f}-{min(hi, 1.0):.2f}"
    return "?"


def report(out_path: str | None) -> str:
    rng = random.Random(SEED)
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT ref_status, count(*) AS n FROM kalshi_settled_market
                       GROUP BY ref_status ORDER BY n DESC""")
        census = {r["ref_status"]: r["n"] for r in cur.fetchall()}
        cur.execute(
            """SELECT ticker, event_ticker, category, close_time, result,
                      ref_yes_bid::float AS bid, ref_yes_ask::float AS ask
               FROM kalshi_settled_market WHERE ref_status = 'ok'
               ORDER BY close_time"""
        )
        rows = list(cur.fetchall())
    if not rows:
        return "no scored rows yet (run backfill + candles first)\n"
    cut = rows[len(rows) // 2]["close_time"]

    def evs(r):
        won = 1 if r["result"] == "yes" else 0
        ev_yes = won - r["ask"] - taker_fee(r["ask"])
        no_cost = 1.0 - r["bid"]
        ev_no = (1 - won) - no_cost - taker_fee(min(max(no_cost, 0.01), 0.99))
        return won, ev_yes, ev_no

    cells = defaultdict(lambda: {"h1": [], "h2": []})
    for r in rows:
        mid = (r["bid"] + r["ask"]) / 2.0
        won, ev_yes, ev_no = evs(r)
        half = "h1" if r["close_time"] < cut else "h2"
        key = (r["category"], _band(mid))
        cells[key][half].append((r["event_ticker"] or r["ticker"], won, mid, ev_yes, ev_no))

    lines = [
        f"# EXP-2026-015 — Venue-Wide Settlement-Calibration Sweep — {date.today()}",
        "",
        f"_generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        "Locked prereg: `EXP_2026_015_VENUE_CALIBRATION_SWEEP.md`. Reference = settlement-eve",
        "daily candle (executable yes bid/ask); taker fees in; cluster bootstrap by",
        "event_ticker; chronological halves; candidate needs BOTH halves independently",
        f"(n>={MIN_CELL_N}, CI excl 0, edge>{MIN_EDGE:.2f}) on the same side.",
        "",
        f"Census by ref_status: {census}",
        f"Scored markets: {len(rows)} | half cut: {cut:%Y-%m-%d}",
        "",
        "| category | band | n1/n2 | win1/win2 | mid | EV_yes h1 | EV_yes h2 | EV_no h1 | EV_no h2 | candidate |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    candidates = []
    for (cat, band), halves in sorted(cells.items()):
        n1, n2 = len(halves["h1"]), len(halves["h2"])
        if n1 + n2 < MIN_CELL_N:
            continue

        def stats(items, idx):
            if not items:
                return None
            vals = [x[idx] for x in items]
            by_cl = defaultdict(list)
            for x in items:
                by_cl[x[0]].append(x[idx])
            lo, hi = cluster_boot_ci(by_cl, rng, n_boot=1000)
            return {"n": len(vals), "mean": sum(vals) / len(vals), "lo": lo, "hi": hi,
                    "win": sum(x[1] for x in items) / len(items),
                    "mid": sum(x[2] for x in items) / len(items)}

        sy1, sy2 = stats(halves["h1"], 3), stats(halves["h2"], 3)
        sn1, sn2 = stats(halves["h1"], 4), stats(halves["h2"], 4)
        cand = ""
        for side, a, b in (("YES", sy1, sy2), ("NO", sn1, sn2)):
            if (a and b and a["n"] >= MIN_CELL_N and b["n"] >= MIN_CELL_N
                    and a["mean"] > MIN_EDGE and b["mean"] > MIN_EDGE
                    and a["lo"] is not None and a["lo"] > 0
                    and b["lo"] is not None and b["lo"] > 0):
                cand = f"**{side}**"
                candidates.append((cat, band, side, a, b))

        def f(s, key="mean"):
            return f"{s[key]:+.3f}" if s else "-"

        w1 = f"{sy1['win']:.2f}" if sy1 else "-"
        w2 = f"{sy2['win']:.2f}" if sy2 else "-"
        midv = sy1["mid"] if sy1 else (sy2["mid"] if sy2 else 0)
        lines.append(
            f"| {cat} | {band} | {n1}/{n2} | {w1}/{w2} | {midv:.3f} | "
            f"{f(sy1)} | {f(sy2)} | {f(sn1)} | {f(sn2)} | {cand} |"
        )
    lines += ["", f"## Candidates: {len(candidates)}", ""]
    if candidates:
        for cat, band, side, a, b in candidates:
            lines.append(
                f"- **{cat} / {band} / buy {side}**: h1 n={a['n']} EV={a['mean']:+.4f} "
                f"CI[{a['lo']:+.4f},{a['hi']:+.4f}]; h2 n={b['n']} EV={b['mean']:+.4f} "
                f"CI[{b['lo']:+.4f},{b['hi']:+.4f}]. Next: forward-window prereg "
                "(>=200 fresh markets), NOT a trading change. PARLAY cells: fee schedule "
                "unverified — confirm before believing the EV."
            )
    else:
        lines.append("None. Per prereg §7 the venue-structure axis closes if this holds.")
    lines.append("")
    md = "\n".join(lines) + "\n"
    if out_path:
        from pathlib import Path
        Path(out_path).write_text(md)
    return md


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--days", type=int, default=90)
    c = sub.add_parser("candles")
    c.add_argument("--limit", type=int, default=8000)
    r = sub.add_parser("report")
    r.add_argument("--out", type=str, default=None)
    args = ap.parse_args()
    if args.cmd == "backfill":
        backfill(args.days)
    elif args.cmd == "candles":
        fetch_candles(args.limit)
    else:
        print(report(args.out))


if __name__ == "__main__":
    main()
