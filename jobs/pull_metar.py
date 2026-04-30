"""Cron job: pull hourly METAR observations."""
import logging
from weather_bot.data import metar_fetcher

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    metar_fetcher.run()
