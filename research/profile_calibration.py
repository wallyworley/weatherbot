"""Profile forecast calibration to identify overconfidence patterns.

Key questions:
1. When did overconfidence start? (date-by-date)
2. Which stations/variables are worst? (KMDW vs KMIA vs KNYC)
3. Which lead times? (same-day vs 1-day vs 2-day)
4. Seasonal pattern? (March vs April vs May)

Output: Bucket-level calibration check — for each bucket that settled,
  actual_outcome ∈ {0,1} vs side-adjusted signal fair probability,
  compute calibration error (pred - actual) per bucket.

Calibration error > 0 = overconfident (predicted too high)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from collections import defaultdict

from psycopg.rows import dict_row

from weather_bot.data import persistence

log = logging.getLogger(__name__)


def profile_calibration(start_date: date = None, end_date: date = None):
    """Analyze calibration errors by date, station, variable, lead_day.

    Returns: dict of calibration metrics keyed by (date, station, var, lead_day)
    """
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=60)  # 60-day window

    sql = """
    -- For each settled fill, match it to:
    -- 1. The market definition (station, var, bucket range)
    -- 2. The forecast probability we assigned at open time
    -- 3. The actual outcome (0/1)
    -- Then compute calibration error (pred - actual)
    WITH fills_with_dist AS (
        SELECT
            pf.ts::date AS trade_date,
            pf.ts,
            m.station,
            m.var,
            pf.side,
            pf.price,
            s.fair_prob,
            CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END AS p_side,
            pf.contracts,
            m.lower_f, m.upper_f,
            COALESCE(pf.payout, 0) AS payout,
            CASE WHEN COALESCE(pf.payout, 0) > 0 THEN 1.0 ELSE 0.0 END AS outcome,
            -- Lead day calculation (local date)
            GREATEST(0,
              (m.valid_date - (pf.ts AT TIME ZONE st.tz)::date)::int
            ) AS lead_day
        FROM paper_fill pf
        JOIN kalshi_market m ON m.ticker = pf.ticker
        JOIN signal s ON s.id = pf.signal_id
        JOIN stations st ON st.code = m.station
        WHERE pf.settled = TRUE
          AND pf.exit_price IS NULL
          AND pf.payout IS NOT NULL
          AND pf.ts::date BETWEEN %(start_date)s AND %(end_date)s
          AND s.fair_prob IS NOT NULL
    )
    SELECT
        trade_date,
        station, var, lead_day,
        COUNT(*) AS n_fills,
        COUNT(DISTINCT side) AS n_sides,
        ROUND(AVG(outcome)::numeric, 3) AS actual_win_rate,
        ROUND(AVG(p_side)::numeric, 3) AS avg_predicted_win_prob,
        ROUND((AVG(p_side) - AVG(outcome))::numeric, 4) AS calibration_error,
        ROUND(STDDEV_SAMP(outcome)::numeric, 3) AS outcome_stdev
    FROM fills_with_dist
    GROUP BY trade_date, station, var, lead_day
    ORDER BY trade_date DESC, station, var, lead_day
    """

    results = defaultdict(list)
    with persistence.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {"start_date": start_date, "end_date": end_date})
            for row in cur.fetchall():
                key = (row["station"], row["var"], row["lead_day"])
                results[key].append(row)

    return results


def print_by_date(results):
    """Print calibration errors grouped by date (most recent first)."""
    print("\n=== CALIBRATION ERROR BY DATE ===")
    print("(Positive = overconfident; negative = underconfident)\n")

    all_rows = []
    for rows in results.values():
        all_rows.extend(rows)
    all_rows.sort(key=lambda r: r["trade_date"], reverse=True)

    for row in all_rows[:100]:  # Last 100 rows
        print(
            f"{row['trade_date']} {row['station']:4s} {row['var']:12s} "
            f"L{row['lead_day']} n={row['n_fills']:2d} "
            f"outcome={row['actual_win_rate']:.3f} "
            f"pred={row['avg_predicted_win_prob']:.3f} "
            f"ERROR={row['calibration_error']:+.4f}"
        )


def print_by_station_var(results):
    """Aggregate by station/var/lead_day; show overall calibration."""
    print("\n=== CALIBRATION ERROR SUMMARY (aggregated) ===\n")

    agg = {}
    for (station, var, lead_day), rows in results.items():
        total_fills = sum(r["n_fills"] for r in rows)
        avg_error = sum(
            r["calibration_error"] * r["n_fills"] / total_fills for r in rows
        )
        agg[(station, var, lead_day)] = (total_fills, avg_error)

    for (station, var, lead_day), (n, error) in sorted(agg.items()):
        print(
            f"{station:4s} {var:12s} L{lead_day} : n={n:3d} fills, "
            f"avg_calibration_error={error:+.4f}"
        )


def print_monthly_breakdown(results):
    """Show if May is worse than April."""
    print("\n=== MONTHLY BREAKDOWN ===\n")

    monthly = defaultdict(lambda: {"fills": 0, "error_sum": 0.0})
    for rows in results.values():
        for row in rows:
            month = row["trade_date"].strftime("%Y-%m")
            monthly[month]["fills"] += row["n_fills"]
            monthly[month]["error_sum"] += float(
                row["calibration_error"] * row["n_fills"]
            )

    for month in sorted(monthly.keys(), reverse=True):
        m = monthly[month]
        avg_err = m["error_sum"] / m["fills"]
        print(
            f"{month}: n={m['fills']:3d} fills, "
            f"avg_calibration_error={avg_err:+.4f}"
        )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--start-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Start date (default: 60 days ago)",
    )
    ap.add_argument(
        "--end-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="End date (default: today)",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    results = profile_calibration(start_date=args.start_date, end_date=args.end_date)

    print_by_date(results)
    print_by_station_var(results)
    print_monthly_breakdown(results)
