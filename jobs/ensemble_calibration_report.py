"""Cron/ad-hoc wrapper for calibrated true-ensemble replay."""
from __future__ import annotations

import argparse
from pathlib import Path

from research import ensemble_calibration


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--per-group-limit", type=int, default=200)
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = ensemble_calibration.run(
        days_back=args.days_back,
        out_dir=args.out_dir,
        limit=args.limit,
        per_group_limit=args.per_group_limit,
    )
    print(Path(result["report_path"]).read_text())
