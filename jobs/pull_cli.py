"""Pull NWS CLI (Daily Climate Report) for each fetch station and persist to cli_obs.

Captures the most recent CLI for each station and the prior day. CLI is the
Kalshi NHIGH settlement authority — having it locally means settle_paper_fills
can use the official TMAX/TMIN instead of the METAR-reconstructed value, which
30-day comparison showed undercounts CLI by 0.5-1°F.

Usage:
    python -m weather_bot.jobs.pull_cli                  # yesterday + day-before
    python -m weather_bot.jobs.pull_cli --days-back 7    # backfill last 7 days

Cadence: daily, ~9 AM ET (after morning CLI issuance window completes ~14 UTC).
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from weather_bot.config import ACTIVE_FETCH_STATIONS
from weather_bot.data import nws_text_products as nws

log = logging.getLogger(__name__)


def run(stations: list[str], days_back: int = 2, record_provenance: bool | None = None) -> int:
    if record_provenance is None:
        # Scheduled live pulls use the default 2-day window. Wider historical
        # backfills should not manufacture first_seen_at evidence for EXP-2026-011.
        record_provenance = days_back <= 2
    today = date.today()
    captured = 0
    for st in stations:
        for i in range(1, days_back + 1):
            d = today - timedelta(days=i)
            r = nws.fetch_cli(st, d)
            if r is None:
                log.warning("CLI %s %s: no product found", st, d)
                continue
            obs, raw, issued = r
            if obs.tmax_f is None and obs.tmin_f is None:
                log.warning("CLI %s %s: parsed both TMAX/TMIN as None", st, d)
                continue
            nws.upsert_cli_obs(st, d, obs, raw, issued, record_provenance=record_provenance)
            log.info("CLI %s %s: tmax=%.1f tmin=%.1f section=%s issued=%s",
                     st, d, obs.tmax_f or float("nan"), obs.tmin_f or float("nan"),
                     obs.section, issued)
            captured += 1
    log.info("pull_cli: captured %d CLI rows across %d stations", captured, len(stations))
    return captured


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", nargs="+", default=ACTIVE_FETCH_STATIONS)
    ap.add_argument("--days-back", type=int, default=2)
    ap.add_argument(
        "--record-provenance",
        action="store_true",
        help="Force EXP-2026-011 first-seen provenance even for wider manual pulls.",
    )
    ap.add_argument(
        "--no-provenance",
        action="store_true",
        help="Disable EXP-2026-011 first-seen provenance for this pull.",
    )
    args = ap.parse_args()
    provenance = None
    if args.record_provenance:
        provenance = True
    if args.no_provenance:
        provenance = False
    run(args.stations, days_back=args.days_back, record_provenance=provenance)
