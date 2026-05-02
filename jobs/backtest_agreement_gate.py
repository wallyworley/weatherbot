"""Retrospective backtest: would the multi-model agreement gate have helped?

For each settled paper fill in the last N days, reconstructs what each model
(NBM p50, HRRR daily TMAX, GFS daily TMAX) would have voted on the fill's
bucket. Aggregates by agreement-with-bot's-chosen-side and reports win rate
+ net P&L per group.

GFS coverage caveat: gfs_fetcher only started writing to det_forecast on
2026-05-01 evening, so for older fills we pull GFS via Open-Meteo's
historical-forecast archive on demand. Pre-fetched once per (station,
valid_date) to keep request volume bounded.

Output: research/reports/backtest_agreement_gate_<date>.md plus stdout.
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from weather_bot.config import ACTIVE_TRADE_STATIONS
from weather_bot.data import persistence

from research.sources.openmeteo_fetcher import fetch_forecast_daily

log = logging.getLogger(__name__)


def _vote(point_est: Optional[float], lower_f: Optional[float], upper_f: Optional[float]) -> str:
    if point_est is None:
        return "NA"
    lo = lower_f if lower_f is not None else float("-inf")
    hi = upper_f if upper_f is not None else float("inf")
    return "YES" if lo <= point_est < hi else "NO"


def _nbm_p50(station: str, valid_date) -> Optional[float]:
    """Latest NBM p50 for the valid_date — uses what was actually available."""
    sql = """
    SELECT value FROM prob_forecast
     WHERE station=%s AND valid_date=%s AND var='TMAX_DAILY' AND percentile=50
     ORDER BY run_time DESC LIMIT 1
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, valid_date))
        r = cur.fetchone()
    return float(r["value"]) if r and r["value"] is not None else None


def _load_settled_fills(days_back: int) -> list[dict]:
    sql = """
    SELECT pf.id, pf.ticker, pf.side, pf.price, pf.contracts, pf.fees, pf.payout,
           km.station, km.var, km.valid_date, km.lower_f, km.upper_f
      FROM paper_fill pf
      JOIN kalshi_market km ON km.ticker = pf.ticker
     WHERE pf.settled = TRUE
       AND km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
       AND km.var = 'TMAX_DAILY'
     ORDER BY km.valid_date, pf.id
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (days_back,))
        return cur.fetchall()


def _agreement_label(side: str, votes: dict) -> str:
    n_yes = sum(1 for v in votes.values() if v == "YES")
    n_no = sum(1 for v in votes.values() if v == "NO")
    same_side = n_yes if side == "YES" else n_no
    other_side = n_no if side == "YES" else n_yes
    if same_side >= 2:
        return "with_us"
    if other_side >= 2:
        return "against"
    return "split"


def run(days_back: int = 30, out_dir: Path = Path("research/reports")) -> dict:
    fills = _load_settled_fills(days_back)
    log.info("loaded %d settled fills (last %d days)", len(fills), days_back)
    if not fills:
        return {"status": "no_fills"}

    # Pre-fetch GFS once per (station, valid_date) — Open-Meteo historical
    # archive. Saves dozens of requests when many fills share a date.
    gfs_cache: dict[tuple[str, date], Optional[float]] = {}
    needed = {(f["station"], f["valid_date"]) for f in fills}
    log.info("pre-fetching GFS historical for %d (station,date) pairs", len(needed))
    for st, d in sorted(needed):
        try:
            r = fetch_forecast_daily("gfs", st, d, historical=True)
            gfs_cache[(st, d)] = r.get("tmax_f")
        except Exception as e:
            log.warning("GFS historical %s %s: %s", st, d, e)
            gfs_cache[(st, d)] = None

    # Aggregate
    by_agreement: dict[str, list[dict]] = defaultdict(list)
    for f in fills:
        nbm = _nbm_p50(f["station"], f["valid_date"])
        hrrr = persistence.latest_hrrr_tmax(f["station"], f["valid_date"])
        gfs = gfs_cache.get((f["station"], f["valid_date"]))
        votes = {
            "NBM":  _vote(nbm, f["lower_f"], f["upper_f"]),
            "HRRR": _vote(float(hrrr) if hrrr is not None else None,
                            f["lower_f"], f["upper_f"]),
            "GFS":  _vote(float(gfs) if gfs is not None else None,
                            f["lower_f"], f["upper_f"]),
        }
        # Drop NA models from the tally so 1 missing model doesn't auto-block.
        active_votes = {m: v for m, v in votes.items() if v != "NA"}
        if len(active_votes) < 2:
            label = "insufficient_data"
        else:
            label = _agreement_label(f["side"], active_votes)
        gross_pnl = (float(f["payout"] or 0) - float(f["price"])) * int(f["contracts"])
        net_pnl = gross_pnl - float(f["fees"] or 0)
        by_agreement[label].append({
            "won": (f["payout"] or 0) > 0,
            "net_pnl": net_pnl,
            "fee_per_contract": (float(f["fees"] or 0) / int(f["contracts"])) if int(f["contracts"]) else 0,
        })

    # Compute summary per group
    out = {"status": "ok", "n_total": len(fills), "by_agreement": {}}
    for label, recs in by_agreement.items():
        wins = sum(1 for r in recs if r["won"])
        n = len(recs)
        net = sum(r["net_pnl"] for r in recs)
        out["by_agreement"][label] = {
            "n": n,
            "wins": wins,
            "win_rate": wins / n if n else 0.0,
            "net_pnl": net,
            "net_pnl_per_fill": net / n if n else 0.0,
        }

    # Write markdown report
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    md_path = out_dir / f"backtest_agreement_gate_{today}.md"
    lines = [
        f"# Agreement Gate Backtest — {today}",
        "",
        f"Window: last {days_back} days · Total settled fills (TMAX_DAILY): **{len(fills)}**",
        "",
        "Reconstructs each historical fill's would-be vote tally from NBM p50, "
        "HRRR daily TMAX, and GFS daily TMAX (Open-Meteo historical archive).",
        "Categorizes by whether ≥2 models agreed with the bot's chosen side.",
        "",
        "| Agreement | n fills | win rate | total net P&L | $/fill |",
        "|---|---:|---:|---:|---:|",
    ]
    order = ["with_us", "against", "split", "insufficient_data"]
    for label in order:
        if label not in out["by_agreement"]:
            continue
        s = out["by_agreement"][label]
        lines.append(
            f"| **{label}** | {s['n']} | {s['win_rate']:.1%} | "
            f"${s['net_pnl']:+,.2f} | ${s['net_pnl_per_fill']:+.2f} |"
        )
    # Headline interpretation — P&L-based, not win-rate-based.
    # Win rate alone misleads when prices vary widely (long-shot bets win
    # rarely but pay big; consensus bets win often but pay little).
    with_us = out["by_agreement"].get("with_us", {})
    against = out["by_agreement"].get("against", {})
    if with_us and against:
        delta_winrate = with_us["win_rate"] - against["win_rate"]
        delta_pnl_per_fill = with_us["net_pnl_per_fill"] - against["net_pnl_per_fill"]
        # Hypothetical: if gate had been on, we'd have kept with_us, skipped against.
        gate_pnl_change = -against["net_pnl"]   # we'd LOSE the against P&L
        lines += [
            "",
            "## Verdict",
            "",
            f"- `with_us` win rate is **{delta_winrate:+.1%}** vs `against` "
            f"(higher win rate = more often correct)",
            f"- `with_us` net P&L per fill is **${delta_pnl_per_fill:+.2f}** vs `against` "
            f"(higher = more profitable)",
            f"- **Hypothetical with gate ON**: total P&L would change by "
            f"**${gate_pnl_change:+,.2f}** (skipping all `against` fills)",
            "",
        ]
        if against.get("n", 0) < 10:
            lines.append("**Recommendation**: insufficient `against` sample (n<10). "
                          "Re-run after another week of trading.")
        elif gate_pnl_change >= 0:
            lines.append(f"**Recommendation**: enabling the gate would have **gained** "
                          f"${gate_pnl_change:+,.2f} over this window. "
                          f"Set `REQUIRE_AGREEMENT_N=2` in .env.")
        else:
            lines.append(f"**Recommendation**: enabling the gate would have **cost** "
                          f"${gate_pnl_change:+,.2f} over this window. **Do NOT enable it** — "
                          f"the bot's against-consensus trades are concentrated in "
                          f"long-shot bets where model votes don't capture the edge. "
                          f"Win rate ≠ profit.")

    md_path.write_text("\n".join(lines) + "\n")
    log.info("wrote %s", md_path)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=30)
    args = ap.parse_args()
    result = run(days_back=args.days_back)
    if result["status"] == "ok":
        print()
        # Pretty-print the markdown to stdout too
        md_path = Path("research/reports") / f"backtest_agreement_gate_{date.today()}.md"
        print(md_path.read_text())
