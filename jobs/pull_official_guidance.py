"""Pull official NWS/NCEP guidance into forecast_guidance.

Research-only: this does not alter live trading or sizing. Run hourly for
NWS_GRID/LAMP/OBS_TRACKER/TAF and every 3-6h is enough for MAV/PFM.
"""
from __future__ import annotations

import argparse
import logging

from weather_bot.data import official_guidance_fetcher


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--stations",
        default="",
        help="Comma-separated station codes. Defaults to active fetch stations plus neighbors.",
    )
    ap.add_argument(
        "--sources",
        default="NWS_GRID,NWS_PFM,LAMP,MAV,OBS_TRACKER,TAF",
        help="Comma-separated subset of NWS_GRID,NWS_PFM,LAMP,MAV,OBS_TRACKER,TAF",
    )
    ap.add_argument(
        "--no-neighbors",
        action="store_true",
        help="Only collect ACTIVE_FETCH_STATIONS, not configured neighbor stations.",
    )
    ap.add_argument(
        "--backfill-days",
        type=int,
        default=0,
        help="Backfill recent NWS_PFM/LAMP/MAV rolling archives for this many days.",
    )
    args = ap.parse_args()
    include_neighbors = not args.no_neighbors
    stations = [s.strip().upper() for s in args.stations.split(",") if s.strip()] or None
    sources = [s.strip().upper() for s in args.sources.split(",") if s.strip()]
    if args.backfill_days > 0:
        n = official_guidance_fetcher.backfill_recent(
            days=args.backfill_days,
            stations=stations,
            sources=sources,
            include_neighbors=include_neighbors,
        )
    else:
        n = official_guidance_fetcher.run(
            stations=stations,
            sources=sources,
            include_neighbors=include_neighbors,
        )
    logging.info("pull_official_guidance: %d rows", n)


if __name__ == "__main__":
    main()
