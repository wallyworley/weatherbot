"""Ad-hoc wrapper for market-information forensics research.

Run this on the VPS where the authoritative PostgreSQL database lives for real
evidence. Do not SSH/tunnel data back to a local machine for evidence
collection. Local runs are code smoke tests only unless explicitly working from
a restored research copy that is labeled as such.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from research import market_information_forensics


def _parse_stations(value: str) -> list[str] | None:
    vals = [s.strip().upper() for s in value.split(",") if s.strip()]
    return vals or None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--max-lead-day", type=int, default=1)
    parser.add_argument("--var", default="TMAX_DAILY")
    parser.add_argument("--stations", default="", help="Comma-separated station codes. Defaults to ACTIVE_FETCH_STATIONS.")
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    parser.add_argument("--tick-minutes", type=int, default=10)
    parser.add_argument("--min-buckets", type=int, default=3)
    parser.add_argument("--include-current", action="store_true")
    parser.add_argument("--climo-lookback-days", type=int, default=60)
    parser.add_argument("--dsm-longitudinal", type=Path, default=Path("research/reports/longitudinal.csv"))
    parser.add_argument("--limit-groups", type=int, default=None)
    args = parser.parse_args()
    result = market_information_forensics.run(
        days_back=args.days_back,
        max_lead_day=args.max_lead_day,
        var=args.var,
        stations=_parse_stations(args.stations),
        out_dir=args.out_dir,
        tick_minutes=args.tick_minutes,
        min_buckets=args.min_buckets,
        include_current=args.include_current,
        climo_lookback_days=args.climo_lookback_days,
        dsm_longitudinal=args.dsm_longitudinal,
        limit_groups=args.limit_groups,
    )
    print(Path(result["report_path"]).read_text())
