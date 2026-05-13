"""Cron/ad-hoc wrapper for the stored forecast benchmark report."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from research import stored_forecast_benchmark


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=30)
    parser.add_argument("--max-lead-day", type=int, default=7)
    args = parser.parse_args()
    result = stored_forecast_benchmark.run(
        days_back=args.days_back,
        max_lead_day=args.max_lead_day,
    )
    print(Path(result["report_path"]).read_text())
