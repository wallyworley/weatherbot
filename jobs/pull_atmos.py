"""Pull atmospheric signals (BL height, 850/925mb temps, cloud, solar) per station.

Cadence: hourly via launchd. Open-Meteo serves the latest GFS run with ~1h lag,
so hourly is the right cadence (matches the GFS fetcher pattern).
"""
from __future__ import annotations

import logging

from weather_bot.data import atmos_fetcher

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    n = atmos_fetcher.run()
    logging.info("pull_atmos: %d rows", n)
