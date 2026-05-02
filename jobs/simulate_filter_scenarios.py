"""Simulate hypothetical entry filters against historical settled fills.

For each named scenario (e.g. "skip lead-1", "raise edge_bps for chalk-consensus"),
partitions the historical fills into kept vs filtered and reports:
- baseline P&L (no filter)
- kept-fills P&L (what we'd have made with filter on)
- filtered-fills P&L (what the filter blocked — positive = we blocked WINNERS, bad)
- delta vs baseline (what the filter would change about total P&L)

Outputs research/reports/filter_scenarios_<date>.md.

Usage:
    python -m weather_bot.jobs.simulate_filter_scenarios --days-back 30
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Callable

from research.sources.openmeteo_fetcher import fetch_forecast_daily
from weather_bot.jobs.analyze_edge_breakdown import _annotate_agreement, _load_fills, FillRecord

log = logging.getLogger(__name__)


# A "filter" is a function that returns True if the fill should be SKIPPED
# (i.e. blocked by the hypothetical entry filter). False = kept = traded.
def _f_skip_lead1(f: FillRecord) -> bool:
    return f.lead_day >= 1


def _f_skip_lead1_unless_strong_edge(f: FillRecord) -> bool:
    # Lead-1 only allowed when edge > $0.04/contract (roughly 400 bps at price 0.10).
    return f.lead_day >= 1 and f.edge < 0.04


def _f_skip_consensus_chalk(f: FillRecord) -> bool:
    return f.agreement == "with_us" and f.price > 0.60


def _f_skip_lead1_or_chalk(f: FillRecord) -> bool:
    return _f_skip_lead1(f) or _f_skip_consensus_chalk(f)


def _f_skip_lead1_unless_strong_or_chalk(f: FillRecord) -> bool:
    return _f_skip_lead1_unless_strong_edge(f) or _f_skip_consensus_chalk(f)


SCENARIOS: dict[str, tuple[str, Callable[[FillRecord], bool]]] = {
    "S1: skip ALL lead-1": (
        "Aggressive — blocks every day-ahead trade. Lead-0 only.",
        _f_skip_lead1,
    ),
    "S2: skip lead-1 unless edge>$0.04": (
        "Conservative — keeps high-edge lead-1 trades. ~75% chance to preserve real edge.",
        _f_skip_lead1_unless_strong_edge,
    ),
    "S3: skip with_us + price>0.60": (
        "Targeted — blocks the 'consensus chalk' leak only. Small dollar impact.",
        _f_skip_consensus_chalk,
    ),
    "S4: skip lead-1 OR with_us-chalk": (
        "Stacks S1 + S3. Most aggressive.",
        _f_skip_lead1_or_chalk,
    ),
    "S5: skip lead-1<edge OR with_us-chalk": (
        "Stacks S2 + S3. Conservative + targeted.",
        _f_skip_lead1_unless_strong_or_chalk,
    ),
}


def _summarize(records: list[FillRecord]) -> dict:
    if not records:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "net_pnl": 0.0}
    n = len(records)
    wins = sum(r.won for r in records)
    return {
        "n": n,
        "wins": wins,
        "win_rate": wins / n,
        "net_pnl": sum(r.net_pnl for r in records),
        "max_loss_blocked": max((r.net_pnl for r in records), default=0.0),
        "max_win_blocked":  min((r.net_pnl for r in records), default=0.0),
    }


def run(days_back: int = 30, out_dir: Path = Path("research/reports")) -> dict:
    fills = _load_fills(days_back)
    log.info("loaded %d fills", len(fills))
    if not fills:
        return {"status": "no_fills"}
    _annotate_agreement(fills)

    baseline = _summarize(fills)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    md_path = out_dir / f"filter_scenarios_{today}.md"

    lines = [
        f"# Filter Scenario Simulator — {today}",
        "",
        f"Window: last {days_back} days · n=**{baseline['n']}** settled TMAX_DAILY fills",
        f"· baseline net P&L=**${baseline['net_pnl']:+,.2f}** · win rate={baseline['win_rate']:.1%}",
        "",
        "Each row = a hypothetical entry filter run against the same fills.",
        "`Δ P&L` = total change vs baseline. **Positive Δ = filter would have HELPED.** "
        "**Negative Δ = filter would have COST money** (blocked winners or kept losers).",
        "",
        "| Scenario | kept_n | kept_$/fill | kept_pnl | filt_n | filt_pnl | **Δ P&L vs baseline** |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    results = {}
    for name, (desc, filter_fn) in SCENARIOS.items():
        kept = [f for f in fills if not filter_fn(f)]
        filtered = [f for f in fills if filter_fn(f)]
        kept_s = _summarize(kept)
        filt_s = _summarize(filtered)
        delta = kept_s["net_pnl"] - baseline["net_pnl"]
        results[name] = {"kept": kept_s, "filtered": filt_s, "delta": delta, "desc": desc}
        kept_per = (kept_s["net_pnl"] / kept_s["n"]) if kept_s["n"] else 0.0
        lines.append(
            f"| **{name}** | {kept_s['n']} | ${kept_per:+.2f} | "
            f"${kept_s['net_pnl']:+,.2f} | {filt_s['n']} | "
            f"${filt_s['net_pnl']:+,.2f} | **${delta:+,.2f}** |"
        )

    # Verdict
    best = max(results.items(), key=lambda kv: kv[1]["delta"])
    worst = min(results.items(), key=lambda kv: kv[1]["delta"])
    lines += [
        "",
        "## Per-scenario detail",
        "",
    ]
    for name, (desc, _) in SCENARIOS.items():
        r = results[name]
        lines.append(f"### {name}")
        lines.append(f"_{desc}_")
        lines.append("")
        lines.append(f"- **{r['filtered']['n']} fills filtered** (n net P&L blocked: ${r['filtered']['net_pnl']:+,.2f})")
        lines.append(f"- **{r['kept']['n']} fills kept** (win rate {r['kept']['win_rate']:.1%}, net P&L ${r['kept']['net_pnl']:+,.2f})")
        lines.append(f"- Δ P&L vs no filter: **${r['delta']:+,.2f}**")
        lines.append("")

    lines += [
        "## Verdict",
        "",
        f"- **Best scenario**: `{best[0]}` (Δ ${best[1]['delta']:+,.2f})",
        f"- **Worst scenario**: `{worst[0]}` (Δ ${worst[1]['delta']:+,.2f})",
        "",
        "**Caveats**:",
        f"- Sample is {baseline['n']} fills — meaningful but not enormous. Single outlier fills can swing scenarios.",
        "- ALL filters that 'help' do so by blocking past losers. The bot's edge could shift; "
        "filters tuned to past data may filter out future profitable trades.",
        "- The honest read: don't ship the 'best' filter just because Δ is positive. "
        "Look at how many *winners* it blocked too — that's the real risk.",
    ]

    md_path.write_text("\n".join(lines) + "\n")
    log.info("wrote %s", md_path)
    return {"status": "ok", "results": results, "report_path": str(md_path)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=30)
    args = ap.parse_args()
    result = run(days_back=args.days_back)
    if result["status"] == "ok":
        print()
        print(Path(result["report_path"]).read_text())
