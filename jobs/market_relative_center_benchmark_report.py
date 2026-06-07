"""Cron/ad-hoc wrapper for the market-relative center benchmark."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from weather_bot.research import market_relative_center_benchmark


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--max-lead-day", type=int, default=3)
    parser.add_argument("--var", choices=("TMAX_DAILY", "TMIN_DAILY"), default="TMAX_DAILY")
    args = parser.parse_args()
    result = market_relative_center_benchmark.run(
        days=args.days,
        max_lead_day=args.max_lead_day,
        var=args.var,
    )
    print(Path(result["report_path"]).read_text())
