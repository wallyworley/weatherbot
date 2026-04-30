"""
Historical backfill of NBM/HRRR/METAR to seed the bias-correction training set.

Usage:
    python -m weather_bot.jobs.backfill_history --days 60

Pulls one NBM QMD cycle per day (12Z) and METAR obs back N days. HRRR
backfill is optional (data volume is large) — enable with --hrrr.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from weather_bot.data import hrrr_fetcher, metar_fetcher, nbm_fetcher

log = logging.getLogger(__name__)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--hrrr", action="store_true", help="Also backfill HRRR (slow)")
    args = ap.parse_args()

    now = datetime.now(tz=timezone.utc)
    for i in range(args.days):
        day = now - timedelta(days=i)
        cycle = day.replace(hour=12, minute=0, second=0, microsecond=0)
        log.info("Backfill NBM cycle %s", cycle)
        try:
            nbm_fetcher.run(target_days=[cycle.date(), cycle.date() + timedelta(days=1)],
                            cycle=cycle)
        except Exception as exc:
            log.warning("NBM backfill failed for %s: %s", cycle, exc)

        if args.hrrr:
            for hh in (0, 6, 12, 18):
                hc = day.replace(hour=hh, minute=0, second=0, microsecond=0)
                try:
                    hrrr_fetcher.run(cycle=hc)
                except Exception as exc:
                    log.warning("HRRR backfill failed for %s: %s", hc, exc)

    # METAR — chunked backfill (72h windows) to avoid API truncation.
    metar_fetcher.backfill(days=args.days)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
