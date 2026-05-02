"""Pull METAR for neighbor stations (spatial triangulation around primaries).

Cadence: every 30 min, in tandem with primary METAR pull. Cheap (one
aviationweather.gov call per neighbor per run, ~10 stations total).
"""
from __future__ import annotations

import argparse
import logging

from weather_bot.data.neighbor_obs import pull_all_neighbors

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=6)
    args = ap.parse_args()
    n = pull_all_neighbors(args.hours)
    logging.info("pull_neighbor_metar: %d rows", n)
