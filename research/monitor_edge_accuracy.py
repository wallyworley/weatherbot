"""Monitor whether the variance fix is improving edge accuracy.

Compares calibration quality PRE-FIX vs POST-FIX on new signals.
Since the fix is now live, we can't directly compare, but we can:
1. Show current calibration errors by lead_day
2. Compare to the pre-fix baseline from earlier today
3. Track edge accuracy (is fair_prob ± margin matching outcomes?)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from psycopg.rows import dict_row

from weather_bot.data import persistence

log = logging.getLogger(__name__)


def get_recent_calibration(hours: int = 24, lead_day: int | None = None) -> dict:
    """Compute recent calibration errors by lead_day.

    For fills settled in the last N hours, compare:
      actual_outcome vs forecast_fairness (derived from market price)
    """
    sql = """
    WITH recent_fills AS (
        SELECT
            pf.ts,
            pf.ts::date AS trade_date,
            m.station,
            m.var,
            pf.side,
            pf.price,
            pf.contracts,
            m.valid_date,
            COALESCE(pf.payout, 0) AS payout,
            -- Lead day (from trade_ts, not settlement_ts)
            (m.valid_date - (pf.ts AT TIME ZONE 'America/New_York')::date)::int AS lead_day
        FROM paper_fill pf
        JOIN kalshi_market m ON m.ticker = pf.ticker
        WHERE pf.settled = TRUE
          AND pf.ts::date >= CURRENT_DATE - INTERVAL '%(hours)s hours'::interval
    )
    SELECT
        lead_day,
        COUNT(*) as n_fills,
        AVG(CASE WHEN side='YES' THEN payout ELSE 1-payout END) as actual_win_rate,
        AVG(price) as avg_market_fair_prob,
        COUNT(CASE WHEN side='YES' AND payout=1 THEN 1 END) +
        COUNT(CASE WHEN side='NO' AND payout=0 THEN 1 END) as n_wins,
        COUNT(*) - (
          COUNT(CASE WHEN side='YES' AND payout=1 THEN 1 END) +
          COUNT(CASE WHEN side='NO' AND payout=0 THEN 1 END)
        ) as n_losses,
        ROUND(
          (AVG(price) - AVG(CASE WHEN side='YES' THEN payout ELSE 1-payout END))::numeric,
          4
        ) as calibration_error
    FROM recent_fills
    WHERE lead_day >= 0
    GROUP BY lead_day
    ORDER BY lead_day
    """

    results = {}
    with persistence.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {"hours": hours})
            for row in cur.fetchall():
                if lead_day is None or row["lead_day"] == lead_day:
                    results[row["lead_day"]] = dict(row)

    return results


def show_edge_accuracy(hours: int = 24):
    """Show whether calibration is improving after the fix."""
    print("\n" + "=" * 100)
    print(f"EDGE ACCURACY MONITOR — Last {hours} hours")
    print("=" * 100 + "\n")

    # Baseline from earlier in the day (before fix was deployed)
    baseline_calibration = {
        0: {"calibration_error": -0.0000, "label": "PERFECT (pre-fix baseline)"},
        1: {"calibration_error": +0.4065, "label": "OVERCONFIDENT by 40.65 bps (pre-fix)"},
    }

    current = get_recent_calibration(hours=hours)

    if not current:
        print("(No settled fills in the last 24 hours)")
        return

    print(f"{'Lead':<6} {'Fills':<8} {'Wins/Losses':<15} {'Win Rate':<12} {'Calibration Error':<20} {'Status':<30}")
    print("-" * 100)

    for lead in sorted(current.keys()):
        row = current[lead]
        baseline = baseline_calibration.get(lead, {})

        error = row["calibration_error"]
        win_rate = row["actual_win_rate"]
        baseline_error = baseline.get("calibration_error", "N/A")

        if isinstance(baseline_error, float):
            if abs(error) < abs(baseline_error) * 0.7:  # 30% improvement
                status = "✅ IMPROVED (fix working!)"
            elif error > baseline_error:
                status = "⚠️  DEGRADED (fix backfire?)"
            else:
                status = "→ No significant change"
        else:
            status = "→ No baseline"

        print(
            f"L{lead:<5} {row['n_fills']:<8} "
            f"{row['n_wins']}/{row['n_losses']:<10} "
            f"{win_rate:.3f} ({win_rate*100:.1f}%) "
            f"{error:+.4f} ({error*10000:+.0f} bps) "
            f"{status:<30}"
        )

    # Summary
    print("\n" + "-" * 100)
    print("INTERPRETATION:")
    print("  • Calibration error = (forecast fair prob) - (actual win rate)")
    print("  • Positive = overconfident (PRE-FIX baseline: L1 was +0.4065)")
    print("  • After fix, L1 should move toward zero (±0.05)")
    print("  • Target: L1 error < ±0.10 (shows variance inflation is working)")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    show_edge_accuracy(hours=args.hours)
