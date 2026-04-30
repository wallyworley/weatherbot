"""Cron job: pull the latest HRRR cycle."""
import logging
from weather_bot.data import hrrr_fetcher

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    hrrr_fetcher.run()
