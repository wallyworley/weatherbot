"""Cron job: read-only Polymarket weather market snapshots."""
import argparse
import logging

from weather_bot.data import polymarket_fetcher


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        help="Polymarket event slug. Defaults to configured NYC/Chicago daily-temp events.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    polymarket_fetcher.run(slugs=args.slug or None)
