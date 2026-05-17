"""Cron job: pull WeatherNext 2 ensemble forecasts into ensemble_forecast."""
import argparse
import logging

from weather_bot.config import ACTIVE_STATIONS
from weather_bot.data import weathernext_fetcher


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", default=",".join(ACTIVE_STATIONS),
                        help="Comma-separated station codes.")
    parser.add_argument("--horizon-days", type=int, default=weathernext_fetcher.HORIZON_DAYS)
    parser.add_argument("--table", default=None,
                        help="Optional fully-qualified BigQuery table override.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stations = [s.strip().upper() for s in args.stations.split(",") if s.strip()]
    count = weathernext_fetcher.run(
        stations=stations,
        horizon_days=args.horizon_days,
        table=args.table,
    )
    if count == 0:
        raise SystemExit("WeatherNext2 produced zero rows; check table/auth/config.")
