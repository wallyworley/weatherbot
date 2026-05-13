"""Where does the bot's edge actually live?

Slices settled paper fills four ways and reports P&L per slice:

1. Probability bin (10 deciles of side-adjusted fair_prob) — extends the
   bin-10 overconfidence finding to "is bin 10 making or losing money?"
2. Price bucket (5 buckets) — where on the price curve are profits/losses
   concentrated? Helps target the FEE_LOAD edge.
3. Lead day (0/1/2/3+) — does the bot do better day-ahead vs intraday?
4. Agreement category (with_us / against / split) — re-confirms backtest
   finding from a slightly different angle, also broken down by price.

Outputs research/reports/edge_breakdown_<date>.md.
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from weather_bot.data import persistence
from weather_bot.models.distribution import lead_day_for_station
from research.sources.openmeteo_fetcher import fetch_forecast_daily

log = logging.getLogger(__name__)


@dataclass
class FillRecord:
    fill_id: int
    side: str
    price: float
    contracts: int
    fees: float
    payout: Optional[float]
    fair_prob: float
    p_side: float                  # side-adjusted: fair_prob for YES, 1-fair_prob for NO
    edge: float
    won: int
    net_pnl: float
    fee_load: float                # effective fee / stake
    station: str
    valid_date: date
    lower_f: Optional[float]
    upper_f: Optional[float]
    lead_day: int
    agreement: str = "unknown"     # filled later by retrospective vote


def _load_fills(days_back: int) -> list[FillRecord]:
    sql = """
    SELECT pf.id, pf.side, pf.price, pf.contracts,
           CEIL((0.07 * pf.contracts * pf.price * (1.0 - pf.price)) * 100) / 100.0 AS fees,
           pf.payout,
           s.fair_prob, s.edge, s.ts AS signal_ts,
           km.station, km.var, km.valid_date, km.lower_f, km.upper_f
      FROM paper_fill pf
      JOIN signal s ON s.id = pf.signal_id
      JOIN kalshi_market km ON km.ticker = pf.ticker
     WHERE pf.settled = TRUE
       AND km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
       AND km.var = 'TMAX_DAILY'
       AND s.fair_prob IS NOT NULL
     ORDER BY km.valid_date, pf.id
    """
    out: list[FillRecord] = []
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (days_back,))
        for r in cur.fetchall():
            price = float(r["price"])
            contracts = int(r["contracts"])
            fees = float(r["fees"] or 0)
            payout = float(r["payout"]) if r["payout"] is not None else None
            won = 1 if (payout and payout > 0) else 0
            net_pnl = (payout - price) * contracts - fees if payout is not None else 0.0
            fair = float(r["fair_prob"])
            p_side = fair if r["side"] == "YES" else 1.0 - fair
            lead_day = lead_day_for_station(r["station"], r["valid_date"], r["signal_ts"])
            out.append(FillRecord(
                fill_id=r["id"], side=r["side"], price=price, contracts=contracts,
                fees=fees, payout=payout, fair_prob=fair, p_side=p_side,
                edge=float(r["edge"]), won=won, net_pnl=net_pnl,
                fee_load=fees / (price * contracts) if price > 0 and contracts > 0 else 0.0,
                station=r["station"], valid_date=r["valid_date"],
                lower_f=r["lower_f"], upper_f=r["upper_f"],
                lead_day=max(0, lead_day),
            ))
    return out


def _annotate_agreement(fills: list[FillRecord]) -> None:
    """In-place fill .agreement using retrospective NBM/HRRR/GFS votes."""
    # Pre-fetch GFS once per (station, valid_date)
    needed = {(f.station, f.valid_date) for f in fills}
    gfs_cache: dict = {}
    log.info("pre-fetching GFS historical for %d (station,date) pairs", len(needed))
    for st, d in sorted(needed):
        try:
            r = fetch_forecast_daily("gfs", st, d, historical=True)
            gfs_cache[(st, d)] = r.get("tmax_f")
        except Exception:
            gfs_cache[(st, d)] = None

    nbm_cache: dict = {}
    hrrr_cache: dict = {}
    for st, d in needed:
        # NBM p50
        with persistence.connect() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT value FROM prob_forecast
                 WHERE station=%s AND valid_date=%s AND var='TMAX_DAILY' AND percentile=50
                 ORDER BY run_time DESC LIMIT 1
            """, (st, d))
            r = cur.fetchone()
            nbm_cache[(st, d)] = float(r["value"]) if r and r["value"] is not None else None
        hrrr_cache[(st, d)] = persistence.latest_hrrr_tmax(st, d)

    def vote(p, lo, hi):
        if p is None:
            return None
        lo_v = lo if lo is not None else float("-inf")
        hi_v = hi if hi is not None else float("inf")
        return "YES" if lo_v <= p < hi_v else "NO"

    for f in fills:
        votes = {
            "NBM":  vote(nbm_cache.get((f.station, f.valid_date)), f.lower_f, f.upper_f),
            "HRRR": vote(hrrr_cache.get((f.station, f.valid_date)), f.lower_f, f.upper_f),
            "GFS":  vote(gfs_cache.get((f.station, f.valid_date)), f.lower_f, f.upper_f),
        }
        active = {m: v for m, v in votes.items() if v is not None}
        if len(active) < 2:
            f.agreement = "insufficient_data"
            continue
        n_yes = sum(1 for v in active.values() if v == "YES")
        n_no = sum(1 for v in active.values() if v == "NO")
        same = n_yes if f.side == "YES" else n_no
        other = n_no if f.side == "YES" else n_yes
        if same >= 2:
            f.agreement = "with_us"
        elif other >= 2:
            f.agreement = "against"
        else:
            f.agreement = "split"


# ---------------------------------------------------------------------------
# Slice aggregators
# ---------------------------------------------------------------------------
@dataclass
class SliceStats:
    n: int = 0
    wins: int = 0
    net_pnl: float = 0.0
    sum_price: float = 0.0
    sum_fee_load: float = 0.0


def _agg(records: list[FillRecord], key_fn) -> dict:
    out: dict = defaultdict(SliceStats)
    for r in records:
        s = out[key_fn(r)]
        s.n += 1
        s.wins += r.won
        s.net_pnl += r.net_pnl
        s.sum_price += r.price
        s.sum_fee_load += r.fee_load
    return out


def _format_slice(out: dict, key_label: str, sort_key=None) -> list[str]:
    keys = sorted(out.keys(), key=sort_key) if sort_key else sorted(out.keys())
    lines = [f"| {key_label} | n | win rate | net P&L | $/fill | avg price | avg fee_load |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for k in keys:
        s = out[k]
        if s.n == 0:
            continue
        lines.append(
            f"| {k} | {s.n} | {s.wins/s.n:.1%} | ${s.net_pnl:+,.2f} | "
            f"${s.net_pnl/s.n:+.2f} | {s.sum_price/s.n:.3f} | {s.sum_fee_load/s.n:.2f} |"
        )
    return lines


def _bin_p_side(p: float) -> str:
    if p >= 1.0:
        return "10  90-100%"
    return f"{int(p*10)+1:>2}  {int(p*10)*10}-{int(p*10)*10+10}%"


def _bucket_price(p: float) -> str:
    if p < 0.20: return "0.00-0.20"
    if p < 0.40: return "0.20-0.40"
    if p < 0.60: return "0.40-0.60"
    if p < 0.80: return "0.60-0.80"
    return "0.80-1.00"


def _bucket_lead(d: int) -> str:
    if d <= 0: return "0 (intraday)"
    if d == 1: return "1 (day ahead)"
    if d == 2: return "2"
    return "3+"


def run(days_back: int = 30, out_dir: Path = Path("research/reports")) -> dict:
    fills = _load_fills(days_back)
    log.info("loaded %d settled TMAX_DAILY fills (last %d days)", len(fills), days_back)
    if not fills:
        return {"status": "no_fills"}
    _annotate_agreement(fills)

    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    md_path = out_dir / f"edge_breakdown_{today}.md"

    total_pnl = sum(f.net_pnl for f in fills)
    total_wins = sum(f.won for f in fills)
    lines = [
        f"# Edge Breakdown — {today}",
        "",
        f"Window: last {days_back} days · TMAX_DAILY fills only · "
        f"n=**{len(fills)}** · wins={total_wins} ({total_wins/len(fills):.1%}) · "
        f"total net P&L=**${total_pnl:+,.2f}**",
        "",
        "Each section slices the same fills a different way. Look for slices "
        "where `$/fill` is meaningfully positive vs negative — that's where the "
        "edge lives or hides.",
        "",
    ]

    # 1. Per-probability bin
    lines += ["## 1. By probability bin (side-adjusted fair_prob)",
              "",
              "Bin 10 = bot was 90-100% confident on the side it bet. "
              "If a bin is profitable, keep trading it. If it's losing, that's "
              "a candidate for cap/skip.",
              ""]
    lines += _format_slice(_agg(fills, lambda r: _bin_p_side(r.p_side)),
                            "bin (p_side)")
    lines.append("")

    # 2. Per-price bucket
    lines += ["## 2. By price bucket",
              "",
              "Where on the price curve do profits / losses concentrate? "
              "Mid-prices (0.40-0.60) carry the highest fee load. Extremes "
              "(0.00-0.20, 0.80-1.00) are long-shots / chalk respectively.",
              ""]
    lines += _format_slice(_agg(fills, lambda r: _bucket_price(r.price)),
                            "price bucket")
    lines.append("")

    # 3. Per-lead-day
    lines += ["## 3. By lead day (signal date → valid date)",
              "",
              "Lead 0 = bot scored the market same day it settles (intraday). "
              "Lead 1+ = day-ahead or further out.",
              ""]
    lines += _format_slice(_agg(fills, lambda r: _bucket_lead(r.lead_day)),
                            "lead day")
    lines.append("")

    # 4. Agreement (re-confirms backtest, but also breaks down by price)
    lines += ["## 4. By model agreement",
              "",
              "Re-confirms `backtest_agreement_gate.py`: bot's profit lives in "
              "against-consensus trades.",
              ""]
    lines += _format_slice(_agg(fills, lambda r: r.agreement), "agreement")
    lines.append("")

    # 5. with_us losers — what specifically is bleeding?
    with_us = [f for f in fills if f.agreement == "with_us"]
    if with_us:
        lines += ["## 5. `with_us` consensus trades — where do they lose?",
                  "",
                  f"Of {len(with_us)} consensus trades, breaking down by price "
                  f"bucket to find the leak.",
                  ""]
        lines += _format_slice(_agg(with_us, lambda r: _bucket_price(r.price)),
                                "price bucket (with_us only)")
        lines.append("")

    # 6. Headline findings
    bin_agg = _agg(fills, lambda r: _bin_p_side(r.p_side))
    bin_10_key = next((k for k in bin_agg if k.startswith("10")), None)
    bin_10 = bin_agg.get(bin_10_key) if bin_10_key else None
    losers = [(k, s) for k, s in bin_agg.items() if s.n >= 5 and s.net_pnl < 0]
    winners = [(k, s) for k, s in bin_agg.items() if s.n >= 5 and s.net_pnl > 5]

    lines += ["## Headline findings", ""]
    if bin_10:
        verdict = "PROFITABLE" if bin_10.net_pnl > 0 else "LOSING"
        lines.append(f"- **Bin 10 (90-100% predicted) is {verdict}**: "
                      f"n={bin_10.n}, win_rate={bin_10.wins/bin_10.n:.0%}, "
                      f"net=${bin_10.net_pnl:+,.2f} ({'${:+.2f}/fill'.format(bin_10.net_pnl/bin_10.n)})")
        if bin_10.net_pnl < -10:
            lines.append("  - **Action**: cap fair_prob at 0.85 inside Kelly sizing — "
                          "the predicted-97% bets aren't winning enough to justify size.")
        elif bin_10.net_pnl > 10:
            lines.append("  - **Action**: leave bin 10 alone — overconfident in win rate "
                          "but still net-profitable in dollars due to high payouts at low cost.")
    if losers:
        lines.append(f"- **Losing bins (n≥5)**: {', '.join(k.split()[1] for k, _ in losers)} — "
                      "candidates for entry filter or sizing reduction.")
    if winners:
        lines.append(f"- **Profitable bins (n≥5, net>$5)**: {', '.join(k.split()[1] for k, _ in winners)}")

    # with_us breakdown verdict
    if with_us:
        wu_agg = _agg(with_us, lambda r: _bucket_price(r.price))
        worst_bucket = min(wu_agg.items(), key=lambda kv: kv[1].net_pnl, default=(None, None))
        if worst_bucket[0] and worst_bucket[1].net_pnl < -5:
            lines.append(
                f"- **Consensus-trade leak**: price bucket `{worst_bucket[0]}` "
                f"in `with_us` is bleeding ${worst_bucket[1].net_pnl:+,.2f} across "
                f"{worst_bucket[1].n} fills (avg fee_load {worst_bucket[1].sum_fee_load/worst_bucket[1].n:.2f}). "
                f"Consider tightening edge_bps threshold for this price band."
            )

    md_path.write_text("\n".join(lines) + "\n")
    log.info("wrote %s", md_path)
    return {"status": "ok", "n_fills": len(fills), "total_pnl": total_pnl,
            "report_path": str(md_path)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=30)
    args = ap.parse_args()
    result = run(days_back=args.days_back)
    if result["status"] == "ok":
        print()
        print(Path(result["report_path"]).read_text())
