"""
Settle paper fills against the observed daily Tmax/Tmin.

Kalshi weather range markets resolve YES if observed value falls in
[lower_f, upper_f). We look up daily_obs for each fill's (station, valid_date, var),
determine which side won, and mark the fill settled with payout $1 (win) or $0 (loss).

Usage:
    python -m weather_bot.jobs.settle_paper_fills
    python -m weather_bot.jobs.settle_paper_fills --dry-run
"""
from __future__ import annotations

import argparse
import logging

from weather_bot.data import persistence

log = logging.getLogger(__name__)


def _yes_wins(lower_f: float | None, upper_f: float | None, obs: float) -> bool:
    """Kalshi temperature ranges. NULL lower = -inf, NULL upper = +inf."""
    if lower_f is not None and obs < lower_f:
        return False
    if upper_f is not None and obs >= upper_f:
        return False
    return True


def _get_daily_obs_value(station: str, valid_date, var: str) -> float | None:
    rows = persistence.get_daily_obs(station, valid_date, valid_date)
    if not rows:
        return None
    r = rows[0]
    return r["tmax_f"] if var == "TMAX_DAILY" else r["tmin_f"]


def run(dry_run: bool = False) -> None:
    fills = persistence.list_unsettled_paper_fills()
    log.info("Found %d unsettled paper fills", len(fills))

    settled = 0
    pending = 0
    wins = 0

    for f in fills:
        obs = _get_daily_obs_value(f["station"], f["valid_date"], f["var"])
        if obs is None:
            pending += 1
            continue  # day hasn't resolved yet

        yes_won = _yes_wins(f["lower_f"], f["upper_f"], obs)
        side_won = (f["side"] == "YES" and yes_won) or (f["side"] == "NO" and not yes_won)
        payout = 1.0 if side_won else 0.0

        notional = f["price"] * f["contracts"]
        gross_pnl = (payout - f["price"]) * f["contracts"]
        net_pnl = gross_pnl - f["fees"]

        log.info(
            "%s %s @%.3f x%d  obs=%.1f  range=[%s,%s)  won=%s  pnl=$%+.2f net=$%+.2f",
            f["ticker"], f["side"], f["price"], f["contracts"], obs,
            f["lower_f"], f["upper_f"], side_won, gross_pnl, net_pnl,
        )

        if not dry_run:
            persistence.settle_paper_fill(f["id"], payout=payout)
        settled += 1
        if side_won:
            wins += 1

    log.info("Settled %d fills (%d wins, %d losses), %d still pending (no obs yet)",
             settled, wins, settled - wins, pending)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Show results without updating DB")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(dry_run=args.dry_run)
