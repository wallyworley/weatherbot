"""Standalone METAR backfill.

Usage:
    python -m weather_bot.jobs.backfill_metar --days 60

Use this to fix an incomplete METAR backfill without re-pulling NBM/HRRR.
"""
from __future__ import annotations

import argparse
import logging

from weather_bot.data import metar_fetcher


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    metar_fetcher.backfill(days=args.days)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
