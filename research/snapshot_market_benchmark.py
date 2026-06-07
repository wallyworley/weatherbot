"""Coherent-snapshot robustness check for the market-relative center benchmark.

RESEARCH-ONLY DIAGNOSTIC. Does not import or modify any trading logic.

As of the 2026-06-06 audit, the coherent-snapshot selection is the CANONICAL
selection inside `market_relative_center_benchmark.py` (run it with the default
`--selection coherent_snapshot`). This module is retained as a thin, focused
wrapper that (a) keeps the `collect_snapshot_bucket_rows` entry point used by
`floor_basis_diagnostic.py` and `gfs_nbm_pit_center.py`, and (b) prints a compact
snapshot-only comparison. The selection logic now lives in one place — the
canonical module — to avoid drift.

Original motivation: the legacy `latest_per_bucket` selection kept, per bucket,
the latest signal that still had a market quote. Dead buckets lose quotes at
different times, so those per-bucket "latest" signals were sampled across a wide
window (median ~9 h at lead 0) and pooled into a distribution the model never
held at one instant. The coherent snapshot picks the latest ~10-minute tick
window in which >= min_buckets buckets were simultaneously live.

Usage:
    python -m weather_bot.research.snapshot_market_benchmark --days 3650 \
        --max-lead-day 7 --tick-minutes 10 --min-buckets 3
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from weather_bot.research.market_relative_center_benchmark import (
    _evidence_statement,
    collect_coherent_snapshot_rows,
    score_events,
    summarize,
    summarize_group,
)


def collect_snapshot_bucket_rows(
    days: int, max_lead_day: int, var: str, tick_minutes: int, min_buckets: int
):
    """One coherent snapshot per event. Delegates to the canonical collector.

    Kept for backwards compatibility with `floor_basis_diagnostic.py` and
    `gfs_nbm_pit_center.py`, which import this function.
    """
    return collect_coherent_snapshot_rows(
        days, max_lead_day, var, tick_minutes=tick_minutes, min_buckets=min_buckets
    )


def run(days: int, max_lead_day: int, var: str, tick_minutes: int, min_buckets: int) -> str:
    rows, diag = collect_snapshot_bucket_rows(days, max_lead_day, var, tick_minutes, min_buckets)
    scores = score_events(rows)
    summaries = summarize(scores)

    lead_groups: dict[int, list] = defaultdict(list)
    for s in scores:
        lead_groups[s.lead_day].append(s)
    lead_summaries = [summarize_group("ALL", lead, vals) for lead, vals in sorted(lead_groups.items())]

    median_spread = diag.get("median_intra_snapshot_spread_h")
    max_spread = diag.get("max_intra_snapshot_spread_h")
    spread_txt = (
        f"{median_spread:.2f} h (max {max_spread:.2f} h)"
        if median_spread is not None and max_spread is not None
        else "n/a"
    )

    lines = [
        f"# Coherent-Snapshot Market Benchmark (research diagnostic) - {date.today()}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"Window: last {days} valid dates; var `{var}`; lead 0-{max_lead_day}; "
        f"tick window {tick_minutes} min; min buckets {min_buckets}.",
        "",
        "Selection is the canonical coherent-snapshot selection; scoring is identical "
        "to the production benchmark.",
        "",
        f"Events with a usable coherent snapshot: {diag['events_with_snapshot']} / "
        f"{diag['events_total']}. Median intra-snapshot bucket time spread: {spread_txt}.",
        "",
        "## Evidence Statement",
        "",
        _evidence_statement("All scored station/lead groups (coherent snapshot)", summaries),
        "",
        "## By Lead Day",
        "",
        "| lead | events | buckets | model Brier | market Brier | diff | model RPS | market RPS | diff | model CRPS | market CRPS | diff | model center MAE | market center MAE | diff |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in lead_summaries:
        lines.append(
            f"| {s.lead_day} | {s.n_events} | {s.avg_buckets:.1f} | "
            f"{s.model_brier:.4f} | {s.market_brier:.4f} | {s.diff_brier:+.4f} | "
            f"{s.model_rps:.4f} | {s.market_rps:.4f} | {s.diff_rps:+.4f} | "
            f"{s.model_crps:.3f} | {s.market_crps:.3f} | {s.diff_crps:+.3f} | "
            f"{s.model_center_mae_f:.2f} | {s.market_center_mae_f:.2f} | {s.diff_center_mae_f:+.2f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=3650)
    p.add_argument("--max-lead-day", type=int, default=7)
    p.add_argument("--var", choices=("TMAX_DAILY", "TMIN_DAILY"), default="TMAX_DAILY")
    p.add_argument("--tick-minutes", type=int, default=10)
    p.add_argument("--min-buckets", type=int, default=3)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()
    md = run(args.days, args.max_lead_day, args.var, args.tick_minutes, args.min_buckets)
    if args.out:
        args.out.write_text(md)
    print(md)


if __name__ == "__main__":
    main()
