"""Nightly: recompute bias tables, then run forecast verification."""
import logging

from weather_bot.models import bias_correction
from weather_bot.verification import metrics

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bias_correction.recompute()
    metrics.run()
