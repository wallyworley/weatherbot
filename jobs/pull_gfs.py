"""Cron job: pull the latest GFS cycle via Open-Meteo."""
import logging

from weather_bot.data import gfs_fetcher

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    gfs_fetcher.run()
