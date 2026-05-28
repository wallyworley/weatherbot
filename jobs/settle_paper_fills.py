"""
Settle paper fills against the observed daily Tmax/Tmin.

Kalshi weather range markets resolve YES if observed value falls in
[lower_f, upper_f). For each fill's (station, valid_date, var) we look up
the official observation and mark the fill settled with payout $1 (win) or $0 (loss).

Source: NWS CLI ONLY. Kalshi settles on the official NWS Climatological
Report (Daily) — anything else risks boundary-bucket settlement errors.

History (2026-05-27): a previous version of this job fell back to METAR
when CLI hadn't been pulled yet. METAR understates CLI by 0.5-1°F on most
days, which silently flipped 28 paper fills to wrong outcomes between
April and May (net +$922 of phantom P&L). Boundary buckets where CLI
exactly equals an edge value (e.g., CLI=64 with bucket [62, 64)) are the
exact case METAR-fallback breaks. Always wait for CLI — being a day late
beats settling on the wrong number.

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


def _get_obs_value(station: str, valid_date, var: str) -> tuple[float | None, str]:
    """Return (CLI_tmax_or_tmin, 'CLI') or (None, 'pending'). No METAR fallback
    — METAR undercounts CLI by 0.5-1°F and silently mis-settles boundary
    buckets (see module docstring). If CLI isn't available yet, defer."""
    if var == "TMAX_DAILY":
        cli = nws.get_cli_tmax(station, valid_date)
    elif var == "TMIN_DAILY":
        cli = nws.get_cli_tmin(station, valid_date)
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
        obs, source = _get_obs_value(f["station"], f["valid_date"], f["var"])
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
