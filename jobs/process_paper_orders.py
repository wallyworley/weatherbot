"""Process pending maker-style paper orders.

Usage:
    python -m weather_bot.jobs.process_paper_orders
"""
from __future__ import annotations

import logging

from weather_bot.strategy.paper_orders import process_pending_orders


def run() -> None:
    summary = process_pending_orders()
    logging.info(
        "Processed pending paper orders: checked=%d filled=%d expired=%d",
        summary.checked,
        summary.filled,
        summary.expired,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()

