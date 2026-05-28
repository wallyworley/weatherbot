"""
Settle paper fills against the observed daily Tmax/Tmin.

Kalshi weather range markets resolve YES if observed value falls in
[lower_f, upper_f). For each fill's (station, valid_date, var) we look up
the official observation and mark the fill settled with payout $1 (win) or $0 (loss).

Source priority:
  1. Kalshi `expiration_value` (the actual settlement number we'd be paid
     against — bottom-line authority)
  2. NWS CLI tmax/tmin (what Kalshi uses under the hood; small risk of
     drift when daytime peak occurs after the last NWS issuance our parser
     can read)
  3. Defer (return None) — never settle on a guess

History:
  - 2026-05-27: removed METAR fallback after it silently flipped 28 fills
    (+$922 phantom P&L) on boundary buckets where METAR undercounted CLI
    by 1°F.
  - 2026-05-28: added Kalshi `expiration_value` as primary source. The
    daily settled-pull (weatherbot-kalshi-settled.timer at 14:00 UTC,
    before settle at 14:23 UTC) populates `kalshi_market.payload.expiration_value`
    for any market settled in the previous 24h.

Usage:
    python -m weather_bot.jobs.settle_paper_fills
    python -m weather_bot.jobs.settle_paper_fills --dry-run
"""
from __future__ import annotations

import argparse
import logging

from weather_bot.data import nws_text_products as nws
from weather_bot.data import persistence

log = logging.getLogger(__name__)


def _yes_wins(lower_f: float | None, upper_f: float | None, obs: float) -> bool:
    """Kalshi temperature ranges. NULL lower = -inf, NULL upper = +inf."""
    if lower_f is not None and obs < lower_f:
        return False
    if upper_f is not None and obs >= upper_f:
        return False
    return True


def _get_obs_value(fill: dict) -> tuple[float | None, str]:
    """Return (observed_value, source) for a fill.

    Priority:
      1. fill['kalshi_settle_value'] — Kalshi's expiration_value from the
         most recent settled-market pull. This IS the value Kalshi paid
         against; if available, no further lookup needed.
      2. NWS CLI tmax/tmin for (station, valid_date).
      3. None — defer to a later run.
    """
    kalshi = fill.get("kalshi_settle_value")
    if kalshi is not None:
        return float(kalshi), "KALSHI"
    var = fill["var"]
    if var == "TMAX_DAILY":
        cli = nws.get_cli_tmax(fill["station"], fill["valid_date"])
    elif var == "TMIN_DAILY":
        cli = nws.get_cli_tmin(fill["station"], fill["valid_date"])
    else:
        cli = None
    if cli is not None:
        return cli, "CLI"
    return None, "pending"


def run(dry_run: bool = False) -> None:
    fills = persistence.list_unsettled_paper_fills()
    log.info("Found %d unsettled paper fills", len(fills))

    settled = 0
    pending = 0
    wins = 0
    by_source: dict[str, int] = {}

    for f in fills:
        obs, source = _get_obs_value(f)
        if obs is None:
            pending += 1
            continue  # day hasn't resolved yet

        yes_won = _yes_wins(f["lower_f"], f["upper_f"], obs)
        side_won = (f["side"] == "YES" and yes_won) or (f["side"] == "NO" and not yes_won)
        payout = 1.0 if side_won else 0.0

        gross_pnl = (payout - f["price"]) * f["contracts"]
        net_pnl = gross_pnl - f["fees"]

        log.info(
            "%s %s @%.3f x%d  obs=%.1f (%s)  range=[%s,%s)  won=%s  pnl=$%+.2f net=$%+.2f",
            f["ticker"], f["side"], f["price"], f["contracts"], obs, source,
            f["lower_f"], f["upper_f"], side_won, gross_pnl, net_pnl,
        )

        if not dry_run:
            persistence.settle_paper_fill(f["id"], payout=payout)
        settled += 1
        by_source[source] = by_source.get(source, 0) + 1
        if side_won:
            wins += 1

    log.info("Settled %d fills (%d wins, %d losses), %d still pending. Sources: %s",
             settled, wins, settled - wins, pending,
             ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Show results without updating DB")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(dry_run=args.dry_run)
