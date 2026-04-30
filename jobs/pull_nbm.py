"""Cron job: pull the latest NBM QMD cycle."""
import logging
from weather_bot.data import nbm_fetcher

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    nbm_fetcher.run()
