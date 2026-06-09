"""EXP-2026-014: is the Kalshi morning price itself miscalibrated (favorite-longshot bias)?

RESEARCH-ONLY. No production change. Locked pre-registration:
docs/research/EXP_2026_014_MARKET_SELF_CALIBRATION.md (registry EXP-2026-014).

A market-only study: the bot's forecast is never an input. Reference snapshot = first
market_snapshot in [valid_date 14:00, 16:00) UTC per ticker. Primary locked rule: per event
(station, valid_date, var) buy 1 YES contract of the highest-mid bucket at the ASK iff
mid >= 0.50, hold to settlement, Kalshi taker fee applied. Mean net P&L per contract with a
95% cluster-bootstrap CI (cluster = station, valid_date; seed 1337; 2000 resamples).

Design-set run (history through 2026-06-08); a design pass only opens the pre-committed
forward window (valid_date >= 2026-06-10, >=300 fresh events). Nothing here trades.

Usage:
    python -m weather_bot.research.market_longshot_bias --out report.md
"""
from __future__ import annotations

import argparse
import math
import random
from collections import defaultdict
from datetime import date, datetime, timezone

from weather_bot.data import persistence

SEED = 1337
N_BOOT = 2000
FAVORITE_MIN_MID = 0.50
LONGSHOT_BAND = (0.10, 0.30)
SPREAD_FILTER = 0.10

_SQL = """
WITH ref AS (
  SELECT DISTINCT ON (ms.ticker)
         ms.ticker, ms.ts, ms.yes_bid, ms.yes_ask, ms.no_bid, ms.no_ask
  FROM market_snapshot ms
  JOIN kalshi_market km ON km.ticker = ms.ticker
  WHERE km.var IN ('TMAX_DAILY', 'TMIN_DAILY')
    AND km.valid_date <= %s
    AND ms.ts >= (km.valid_date::timestamp AT TIME ZONE 'UTC') + interval '14 hours'
    AND ms.ts <  (km.valid_date::timestamp AT TIME ZONE 'UTC') + interval '16 hours'
    AND ms.yes_bid IS NOT NULL AND ms.yes_ask IS NOT NULL AND ms.yes_ask > 0
  ORDER BY ms.ticker, ms.ts
)
SELECT km.station, km.valid_date, km.var, km.ticker, km.lower_f, km.upper_f,
       ref.yes_bid, ref.yes_ask, ref.no_bid, ref.no_ask,
       CASE WHEN km.var = 'TMAX_DAILY' THEN c.tmax_f ELSE c.tmin_f END AS truth
FROM ref
JOIN kalshi_market km ON km.ticker = ref.ticker
LEFT JOIN cli_obs c ON c.station = km.station AND c.local_date = km.valid_date
"""


def taker_fee(price: float) -> float:
    """Kalshi taker fee per contract, rounded up to the cent."""
    return math.ceil(7.0 * price * (1.0 - price)) / 100.0


def _won(truth: float, lower, upper) -> int:
    if lower is not None and truth < lower:
        return 0
    if upper is not None and truth >= upper:
        return 0
    return 1


def fetch_rows(through: date) -> tuple[list[dict], int]:
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(_SQL, (through,))
        raw = [dict(r) for r in cur.fetchall()]
    rows, n_no_truth = [], 0
    for r in raw:
        if r["truth"] is None:
            n_no_truth += 1
            continue
        r["yes_bid"] = float(r["yes_bid"])
        r["yes_ask"] = float(r["yes_ask"])
        r["no_bid"] = float(r["no_bid"]) if r["no_bid"] is not None else None
        r["no_ask"] = float(r["no_ask"]) if r["no_ask"] is not None else None
        r["mid"] = (r["yes_bid"] + r["yes_ask"]) / 2.0
        r["won"] = _won(float(r["truth"]), r["lower_f"], r["upper_f"])
        r["cluster"] = (r["station"], r["valid_date"])
        rows.append(r)
    return rows, n_no_truth


def cluster_boot_ci(values_by_cluster: dict, rng: random.Random,
                    n_boot: int = N_BOOT) -> tuple[float | None, float | None]:
    clusters = [v for v in values_by_cluster.values() if v]
    if len(clusters) < 2:
        return None, None
    means = []
    k = len(clusters)
    for _ in range(n_boot):
        sample = [clusters[rng.randrange(k)] for _ in range(k)]
        flat = [x for c in sample for x in c]
        means.append(sum(flat) / len(flat))
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]


def _by_cluster(pairs: list[tuple[tuple, float]]) -> dict:
    out = defaultdict(list)
    for c, v in pairs:
        out[c].append(v)
    return out


def favorite_trades(rows: list[dict]) -> list[dict]:
    """The locked primary rule applied to every event."""
    events = defaultdict(list)
    for r in rows:
        events[(r["station"], r["valid_date"], r["var"])].append(r)
    trades = []
    for (station, vdate, var), brows in events.items():
        fav = max(brows, key=lambda r: r["mid"])
        if fav["mid"] < FAVORITE_MIN_MID:
            continue
        pnl = fav["won"] - fav["yes_ask"] - taker_fee(fav["yes_ask"])
        trades.append({
            "station": station, "valid_date": vdate, "var": var,
            "cluster": (station, vdate), "ask": fav["yes_ask"], "mid": fav["mid"],
            "spread": fav["yes_ask"] - fav["yes_bid"], "won": fav["won"],
            "pnl": pnl, "pnl_feefree": fav["won"] - fav["yes_ask"],
            "pnl_midfill": fav["won"] - fav["mid"],
        })
    return trades


def _summ(trades: list[dict], key: str, rng: random.Random) -> dict | None:
    if not trades:
        return None
    vals = [t[key] for t in trades]
    lo, hi = cluster_boot_ci(_by_cluster([(t["cluster"], t[key]) for t in trades]), rng)
    return {"n": len(trades), "mean": sum(vals) / len(vals),
            "win_rate": sum(t["won"] for t in trades) / len(trades),
            "ci": (lo, hi)}


def _fmt(s: dict | None, label: str) -> str:
    if s is None:
        return f"| {label} | 0 | | | |"
    lo, hi = s["ci"]
    ci = f"[{lo:+.4f}, {hi:+.4f}]" if lo is not None else ""
    return (f"| {label} | {s['n']} | {s['win_rate']:.3f} | "
            f"{s['mean']:+.4f} | {ci} |")


def run(through: date) -> str:
    rng = random.Random(SEED)
    rows, n_no_truth = fetch_rows(through)
    trades = favorite_trades(rows)
    trades.sort(key=lambda t: (t["valid_date"], t["station"], t["var"]))

    lines = [
        f"# EXP-2026-014 — Kalshi Market Self-Calibration (design set) — {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        "Locked prereg: `EXP_2026_014_MARKET_SELF_CALIBRATION.md`. Market-only study;",
        "the bot's forecast is never an input. Primary = buy 1 YES of the highest-mid",
        f"bucket at the ASK iff mid >= {FAVORITE_MIN_MID}, hold to settlement, taker fee",
        "included. CIs are cluster-bootstrap by (station, valid_date), seed 1337.",
        "",
        f"Universe: {len(rows)} scored bucket rows through {through} "
        f"({n_no_truth} excluded for missing CLI truth).",
        "",
        "## Primary (LOCKED): morning favorite at the ask, fees in",
        "",
        "| slice | n | win rate | net P&L / contract | 95% cluster CI |",
        "|---|---:|---:|---:|---|",
        _fmt(_summ(trades, "pnl", rng), "ALL (primary)"),
    ]
    # Chronological halves (locked design-pass requirement).
    dates = sorted({t["valid_date"] for t in trades})
    if dates:
        cut = dates[len(dates) // 2]
        first = [t for t in trades if t["valid_date"] < cut]
        second = [t for t in trades if t["valid_date"] >= cut]
        lines += [
            _fmt(_summ(first, "pnl", rng), f"first half (< {cut})"),
            _fmt(_summ(second, "pnl", rng), f"second half (>= {cut})"),
        ]
    for var in ("TMAX_DAILY", "TMIN_DAILY"):
        lines.append(_fmt(_summ([t for t in trades if t["var"] == var], "pnl", rng), var))
    lines += [
        _fmt(_summ([t for t in trades if t["spread"] <= SPREAD_FILTER], "pnl", rng),
             f"spread <= {SPREAD_FILTER} (diagnostic)"),
        _fmt(_summ(trades, "pnl_feefree", rng), "fee-free (diagnostic)"),
        _fmt(_summ(trades, "pnl_midfill", rng), "mid fill, no fee (diagnostic)"),
    ]

    # Per-station means (>=10 events) and the >=60%-positive criterion.
    by_st = defaultdict(list)
    for t in trades:
        by_st[t["station"]].append(t["pnl"])
    st_rows = [(st, len(v), sum(v) / len(v)) for st, v in sorted(by_st.items()) if len(v) >= 10]
    n_pos = sum(1 for _, _, m in st_rows if m > 0)
    lines += [
        "",
        f"## Per-station (>=10 events): {n_pos}/{len(st_rows)} positive "
        f"({(100 * n_pos / len(st_rows)):.0f}% — design pass needs >=60%)" if st_rows else
        "## Per-station: insufficient",
        "",
        "| station | n | mean net P&L |",
        "|---|---:|---:|",
    ]
    for st, n, m in st_rows:
        lines.append(f"| {st} | {n} | {m:+.4f} |")

    # Decile calibration table (diagnostic), all bucket rows.
    lines += [
        "",
        "## Decile calibration (diagnostic; all buckets; edge = win rate − mid)",
        "",
        "| decile | n | avg mid | win rate | edge | 95% cluster CI (edge) |",
        "|---|---:|---:|---:|---:|---|",
    ]
    by_dec = defaultdict(list)
    for r in rows:
        d = min(9, int(r["mid"] * 10))
        by_dec[d].append(r)
    for d in sorted(by_dec):
        rs = by_dec[d]
        mids = [r["mid"] for r in rs]
        wins = [r["won"] for r in rs]
        edges = [(r["cluster"], r["won"] - r["mid"]) for r in rs]
        lo, hi = cluster_boot_ci(_by_cluster(edges), rng)
        ci = f"[{lo:+.4f}, {hi:+.4f}]" if lo is not None else ""
        lines.append(
            f"| {d / 10:.1f}–{(d + 1) / 10:.1f} | {len(rs)} | {sum(mids) / len(rs):.3f} | "
            f"{sum(wins) / len(rs):.3f} | {sum(wins) / len(rs) - sum(mids) / len(rs):+.4f} | {ci} |"
        )

    # Longshot NO side (diagnostic).
    lo_b, hi_b = LONGSHOT_BAND
    no_trades = []
    for r in rows:
        if lo_b <= r["mid"] < hi_b and r["no_ask"] is not None and r["no_ask"] > 0:
            pnl = (1 - r["won"]) - r["no_ask"] - taker_fee(r["no_ask"])
            no_trades.append({"cluster": r["cluster"], "won": 1 - r["won"], "pnl": pnl})
    lines += [
        "",
        f"## Longshot NO side (diagnostic): buy NO at ask on mid in [{lo_b}, {hi_b})",
        "",
        "| slice | n | win rate | net P&L / contract | 95% cluster CI |",
        "|---|---:|---:|---:|---|",
        _fmt(_summ(no_trades, "pnl", rng), "buy NO on longshots"),
    ]
    lines += [
        "",
        "Design-pass requires (prereg §7): primary CI excluding 0 AND both halves positive",
        "AND >=60% stations positive. A pass only opens the forward window",
        "(valid_date >= 2026-06-10, >=300 fresh events). No production change; nothing trades.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--through", type=str, default="2026-06-08",
                   help="design-set end date (inclusive)")
    p.add_argument("--out", type=str, default=None)
    args = p.parse_args()
    md = run(date.fromisoformat(args.through))
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
