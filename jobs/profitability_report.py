"""Profitability research report.

This report covers the high-leverage ideas that should stay research-only
until their dollar impact is proven:

1. Maker/wait-for-better-entry replay from market snapshots
2. Early-exit opportunities at 70% of max gain
3. Divergence-skip replay with corrected order-level fees

Usage:
    python -m weather_bot.jobs.profitability_report --days-back 30
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

from psycopg.rows import dict_row

from weather_bot.data import persistence
from weather_bot.strategy.ev import fee_for_order

log = logging.getLogger(__name__)


def _rows(sql: str, params: dict | tuple) -> list[dict]:
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _maker_wait_replay(days_back: int, improvement_cents: int = 1) -> list[dict]:
    """Wait-for-one-cent-better cross replay using stored snapshots.

    This is a conservative proxy for maker-first execution: it only counts a
    fill if a later top-of-book cross was available at the improved price.
    """
    sql = """
    WITH fills AS (
        SELECT pf.id, pf.ts, pf.ticker, pf.side, pf.price, pf.contracts,
               CEIL((0.07 * pf.contracts * pf.price * (1.0 - pf.price)) * 100) / 100.0 AS fees,
               pf.payout,
               km.valid_date,
               GREATEST(0.01, pf.price - (%(improvement_cents)s / 100.0)) AS target_price
         FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = TRUE
           AND pf.exit_price IS NULL
           AND pf.payout IS NOT NULL
           AND km.valid_date >= CURRENT_DATE - (%(days_back)s || ' days')::interval
    ), first_better AS (
        SELECT f.id, MIN(ms.ts) AS fill_ts
          FROM fills f
          JOIN market_snapshot ms
            ON ms.ticker = f.ticker
           AND ms.ts > f.ts
           AND ms.ts <= (f.valid_date + INTERVAL '1 day')
           AND (
                (f.side = 'YES' AND ms.yes_ask IS NOT NULL AND ms.yes_ask <= f.target_price)
             OR (f.side = 'NO'  AND (
                    (ms.no_ask IS NOT NULL AND ms.no_ask <= f.target_price)
                 OR (ms.yes_bid IS NOT NULL AND (1.0 - ms.yes_bid) <= f.target_price)
             ))
           )
         GROUP BY f.id
    )
    SELECT f.*, fb.fill_ts
      FROM fills f
      LEFT JOIN first_better fb ON fb.id = f.id
     ORDER BY f.ts
    """
    return _rows(sql, {"days_back": days_back, "improvement_cents": improvement_cents})


def _early_exit_replay(days_back: int, gain_fraction: float = 0.70) -> list[dict]:
    """Historical first-hit replay for exiting at a fraction of max gain."""
    sql = """
    WITH fills AS (
        SELECT pf.id, pf.ts, pf.ticker, pf.side, pf.price, pf.contracts,
               CEIL((0.07 * pf.contracts * pf.price * (1.0 - pf.price)) * 100) / 100.0 AS fees,
               pf.payout, km.valid_date,
               pf.price + %(gain_fraction)s * (1.0 - pf.price) AS target_exit
         FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
         WHERE pf.settled = TRUE
           AND pf.exit_price IS NULL
           AND pf.payout IS NOT NULL
           AND km.valid_date >= CURRENT_DATE - (%(days_back)s || ' days')::interval
    ), first_exit AS (
        SELECT f.id, MIN(ms.ts) AS exit_ts
          FROM fills f
          JOIN market_snapshot ms
            ON ms.ticker = f.ticker
           AND ms.ts > f.ts
           AND ms.ts <= (f.valid_date + INTERVAL '1 day')
           AND (
                (f.side='YES' AND ms.yes_bid IS NOT NULL AND ms.yes_bid >= f.target_exit)
             OR (f.side='NO' AND (
                    (ms.no_bid IS NOT NULL AND ms.no_bid >= f.target_exit)
                 OR (ms.yes_ask IS NOT NULL AND (1.0 - ms.yes_ask) >= f.target_exit)
             ))
           )
         GROUP BY f.id
    )
    SELECT f.*, fe.exit_ts
      FROM fills f
      LEFT JOIN first_exit fe ON fe.id = f.id
     ORDER BY f.ts
    """
    return _rows(sql, {"days_back": days_back, "gain_fraction": gain_fraction})


def _divergence_replay(days_back: int, bankroll_usd: float = 20.0) -> list[dict]:
    """Replay DIVERGENCE skips as if they had been filled at top of book."""
    sql = """
    WITH div AS (
        SELECT s.id, s.ts, s.ticker, s.side, s.fair_prob, s.market_ask, s.market_bid,
               km.station, km.var, km.valid_date, km.lower_f, km.upper_f
          FROM signal s
          JOIN kalshi_market km ON km.ticker = s.ticker
         WHERE s.skip_reason = 'DIVERGENCE'
           AND km.valid_date >= CURRENT_DATE - (%(days_back)s || ' days')::interval
           AND km.var = 'TMAX_DAILY'
    ), obs AS (
        SELECT d.*, COALESCE(c.tmax_f, o.tmax_f) AS obs_tmax
          FROM div d
          LEFT JOIN cli_obs c ON c.station = d.station AND c.local_date = d.valid_date
          LEFT JOIN daily_obs o ON o.station = d.station AND o.local_date = d.valid_date
    )
    SELECT * FROM obs
     WHERE obs_tmax IS NOT NULL
     ORDER BY ts
    """
    rows = _rows(sql, {"days_back": days_back})
    out = []
    for r in rows:
        if r["side"] == "YES":
            price = r["market_ask"]
        else:
            price = None if r["market_bid"] is None else 1.0 - float(r["market_bid"])
        if price is None or price <= 0 or price >= 1:
            continue
        price = float(price)
        contracts = max(1, int(bankroll_usd / price))
        fee = fee_for_order(price, contracts)
        obs = float(r["obs_tmax"])
        yes_won = (r["lower_f"] is None or obs >= float(r["lower_f"])) and (
            r["upper_f"] is None or obs < float(r["upper_f"])
        )
        won = yes_won if r["side"] == "YES" else not yes_won
        payout = 1.0 if won else 0.0
        pnl = (payout - price) * contracts - fee
        yes_mid = None
        if r["market_ask"] is not None and r["market_bid"] is not None:
            yes_mid = (float(r["market_ask"]) + float(r["market_bid"])) / 2.0
        divergence = None if yes_mid is None else abs(float(r["fair_prob"]) - yes_mid)
        if divergence is None:
            band = "unknown"
        elif divergence < 0.60:
            band = "50-60pp"
        elif divergence < 0.70:
            band = "60-70pp"
        else:
            band = "70pp+"
        out.append({
            **r,
            "price": price,
            "contracts": contracts,
            "fee": fee,
            "won": won,
            "pnl": pnl,
            "divergence": divergence,
            "divergence_band": band,
        })
    return out


def _maker_summary(rows: list[dict], improvement_cents: int) -> dict:
    filled = [r for r in rows if r["fill_ts"] is not None]
    missed = [r for r in rows if r["fill_ts"] is None]
    improvement = improvement_cents / 100.0
    return {
        "improvement_cents": improvement_cents,
        "reviewed": len(rows),
        "filled": len(filled),
        "missed": len(missed),
        "fill_rate": len(filled) / len(rows) if rows else 0.0,
        "gross_savings": sum(float(r["contracts"]) * improvement for r in filled),
        "missed_pnl": sum(
            (float(r["payout"] or 0) - float(r["price"])) * int(r["contracts"]) - float(r["fees"] or 0)
            for r in missed
        ),
    }


def _exit_summary(rows: list[dict], gain_fraction: float) -> dict:
    hits = [r for r in rows if r["exit_ts"] is not None]
    exit_pnl = sum(
        (float(r["target_exit"]) - float(r["price"])) * int(r["contracts"]) - float(r["fees"] or 0)
        for r in hits
    )
    hold_pnl_for_hits = sum(
        (float(r["payout"] or 0) - float(r["price"])) * int(r["contracts"]) - float(r["fees"] or 0)
        for r in hits
    )
    return {
        "gain_fraction": gain_fraction,
        "reviewed": len(rows),
        "hits": len(hits),
        "hit_rate": len(hits) / len(rows) if rows else 0.0,
        "exit_pnl": exit_pnl,
        "hold_pnl_for_hits": hold_pnl_for_hits,
        "delta": exit_pnl - hold_pnl_for_hits,
    }


def _divergence_group_summary(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["station"], row["side"], row["divergence_band"])].append(row)
    out = []
    for (station, side, band), vals in sorted(groups.items()):
        wins = sum(1 for r in vals if r["won"])
        pnl = sum(float(r["pnl"]) for r in vals)
        out.append({
            "station": station,
            "side": side,
            "band": band,
            "n": len(vals),
            "win_rate": wins / len(vals) if vals else 0.0,
            "pnl": pnl,
            "pnl_per_trade": pnl / len(vals) if vals else 0.0,
        })
    return out


def _fmt_money(x: float) -> str:
    return f"${x:+,.2f}"


def run(days_back: int = 30, out_dir: Path = Path("research/reports")) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    path = out_dir / f"profitability_report_{today}.md"

    maker_summaries = [
        _maker_summary(_maker_wait_replay(days_back, improvement_cents=cents), cents)
        for cents in (1, 2, 3)
    ]
    exit_summaries = [
        _exit_summary(_early_exit_replay(days_back, gain_fraction=frac), frac)
        for frac in (0.50, 0.70, 0.85)
    ]
    divs = _divergence_replay(days_back)

    div_pnl = sum(float(r["pnl"]) for r in divs)
    div_wins = sum(1 for r in divs if r["won"])

    lines = [
        f"# Profitability Report — {today}",
        "",
        f"Window: last {days_back} days.",
        "",
        "## 1. Maker / wait-for-better-entry replay",
        "",
        "Conservative proxy: count a fill only if a later snapshot crossed better than the actual entry price.",
        "",
        "| improvement | reviewed | filled | fill rate | missed | gross savings | missed-fill P&L |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in maker_summaries:
        lines.append(
            f"| {row['improvement_cents']}c | {row['reviewed']} | {row['filled']} | "
            f"{row['fill_rate']:.1%} | {row['missed']} | {_fmt_money(row['gross_savings'])} | "
            f"{_fmt_money(row['missed_pnl'])} |"
        )
    lines.extend([
        "",
        "## 2. Early-exit replay",
        "",
        "Exit rule: first snapshot where mark-to-market reaches a fraction of max gain.",
        "",
        "| threshold | reviewed | hits | hit rate | exit P&L | held P&L for hits | delta |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in exit_summaries:
        lines.append(
            f"| {row['gain_fraction']:.0%} | {row['reviewed']} | {row['hits']} | "
            f"{row['hit_rate']:.1%} | {_fmt_money(row['exit_pnl'])} | "
            f"{_fmt_money(row['hold_pnl_for_hits'])} | {_fmt_money(row['delta'])} |"
        )
    lines.extend([
        "",
        "## 3. Divergence replay",
        "",
        "Replay DIVERGENCE skips as fixed-size paper entries with corrected order-level fees.",
        "",
        f"- Replayable divergence skips: {len(divs)}",
        f"- Win rate: {(div_wins / len(divs)):.1%}" if divs else "- Win rate: n/a",
        f"- Net P&L: {_fmt_money(div_pnl)}",
        "",
        "| station | side | divergence band | n | win rate | net P&L | P&L/trade |",
        "|---|---|---|---:|---:|---:|---:|",
    ])
    for row in _divergence_group_summary(divs):
        lines.append(
            f"| {row['station']} | {row['side']} | {row['band']} | {row['n']} | "
            f"{row['win_rate']:.1%} | {_fmt_money(row['pnl'])} | {_fmt_money(row['pnl_per_trade'])} |"
        )
    lines.extend([
        "",
        "## Suggested interpretation",
        "",
        "- Ship maker-first only if savings are positive after accounting for missed-fill P&L and fill rate stays acceptable.",
        "- Ship early exits only if the exit delta remains positive across multiple thresholds.",
        "- Never enable all DIVERGENCE automatically; use grouped results to identify a narrow station/side/band exception.",
    ])
    path.write_text("\n".join(lines) + "\n")
    log.info("wrote %s", path)
    return {"status": "ok", "report_path": str(path)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=30)
    args = ap.parse_args()
    result = run(days_back=args.days_back)
    if result["status"] == "ok":
        print(Path(result["report_path"]).read_text())
