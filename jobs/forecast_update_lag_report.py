"""Cron/ad-hoc wrapper for forecast-update lag research."""
from __future__ import annotations

import argparse
from pathlib import Path

from research import forecast_update_lag


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--limit", type=int, default=2500)
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = forecast_update_lag.run(days_back=args.days_back, out_dir=args.out_dir, limit=args.limit)
    print(Path(result["report_path"]).read_text())
