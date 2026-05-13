"""Cron/ad-hoc wrapper for the shadow ensemble replay report."""
from __future__ import annotations

import argparse
from pathlib import Path

from research import shadow_ensemble


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--per-group-limit", type=int, default=200)
    args = parser.parse_args()
    result = shadow_ensemble.run(
        days_back=args.days_back,
        limit=args.limit,
        per_group_limit=args.per_group_limit,
    )
    print(Path(result["report_path"]).read_text())
