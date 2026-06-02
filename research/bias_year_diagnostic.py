"""
Per-year bias decomposition — answers the model-version weighting question.

The bias tables are month-keyed and pool all years. Before we pool multi-year
backfill data, we need to know: is the NBM forecast bias STATIONARY across
years (→ pool, equal weight, enjoy the bigger sample), or is there a structural
BREAK (→ a model upgrade shifted it; down-weight the old years)?

For each (month, lead) this prints the mean forecast error (fcst - obs) per
YEAR with sample sizes, plus the spread across years. A small spread = years
agree = safe to pool. A jump at a particular year = candidate model regime
change to cross-check against NBM version dates.

Read-only. Run anytime; sharpens as the backfill fills more years.

Usage:
    python -m weather_bot.research.bias_year_diagnostic
    python -m weather_bot.research.bias_year_diagnostic --var TMIN_DAILY --lead 0
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from weather_bot.data import persistence

_SQL = """
WITH paired AS (
    SELECT EXTRACT(MONTH FROM pf.valid_date)::int AS month,
           EXTRACT(YEAR  FROM pf.valid_date)::int AS year,
           GREATEST(0, (pf.valid_date - pf.run_time::date)::int) AS lead_day,
           pf.value - CASE WHEN pf.var = 'TMAX_DAILY' THEN o.tmax_f
                           ELSE o.tmin_f END AS err
      FROM prob_forecast pf
      JOIN daily_obs o ON o.station = pf.station AND o.local_date = pf.valid_date
     WHERE pf.percentile = 50 AND pf.var = %s
)
SELECT month, year, COUNT(*) AS n, AVG(err) AS bias, STDDEV_SAMP(err) AS sd
  FROM paired
 WHERE lead_day = %s
 GROUP BY month, year
 ORDER BY month, year
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--var", default="TMAX_DAILY", choices=["TMAX_DAILY", "TMIN_DAILY"])
    ap.add_argument("--lead", type=int, default=0)
    args = ap.parse_args()

    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(_SQL, (args.var, args.lead))
        rows = cur.fetchall()

    by_month: dict[int, dict[int, tuple]] = defaultdict(dict)
    years = set()
    for r in rows:
        by_month[r["month"]][r["year"]] = (float(r["bias"]), int(r["n"]))
        years.add(r["year"])
    years = sorted(years)

    print(f"\nPER-YEAR BIAS (fcst - obs, F) — {args.var} lead={args.lead}, p50, "
          f"across stations")
    print("small spread across years = pool; a jump at a year = model regime change\n")
    hdr = "  mon  " + "".join(f"{y:>14}" for y in years) + f"{'spread':>9}"
    print(hdr)
    for m in sorted(by_month):
        cells = []
        biases = []
        for y in years:
            if y in by_month[m]:
                b, n = by_month[m][y]
                biases.append(b)
                cells.append(f"{b:+.2f}/n{n}")
            else:
                cells.append("-")
        spread = (max(biases) - min(biases)) if len(biases) >= 2 else float("nan")
        row = f"  {m:>3}  " + "".join(f"{c:>14}" for c in cells)
        row += f"{spread:>9.2f}" if spread == spread else f"{'-':>9}"
        print(row)
    print("\n(spread = max-min mean bias across years with data; "
          ">~1.5F suggests a non-stationary cell worth time-decay weighting)")


if __name__ == "__main__":
    main()
