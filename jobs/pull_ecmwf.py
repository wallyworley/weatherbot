"""Cron job: pull the latest ECMWF cycle via Open-Meteo."""
import logging

from weather_bot.data import ecmwf_fetcher

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ecmwf_fetcher.run()
