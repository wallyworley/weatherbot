"""Print the current variance-fix methodology and validation checklist."""
from __future__ import annotations


def show_variance_fix_report():
    """Display the lead-aware variance fix report."""

    print("\n" + "=" * 100)
    print("LEAD-AWARE VARIANCE FIX REPORT")
    print("Updated: 2026-05-07")
    print("=" * 100)

    print("\nPART 1: CORRECTED BASELINE")
    print("-" * 100)
    print("""
The old diagnosis used trade price as a proxy for fair probability and inverted
NO payouts. The corrected baseline uses side-adjusted signal fair probability:

  p_side = fair_prob for YES fills, 1 - fair_prob for NO fills
  outcome = 1 when paper_fill.payout > 0, else 0

Apr 1-May 6 settled fills:

  Lead  Fills  Predicted win  Observed win  Error
  L0    33     0.500          0.394         +0.106
  L1    96     0.700          0.542         +0.159

By station for L1:

  KNYC  n=73  error=+0.164
  KMIA  n=18  error=+0.098
  KMDW  n=5   error=+0.308
""")

    print("\nPART 2: CURRENT FIX")
    print("-" * 100)
    print("""
models/distribution.py applies lead-aware spread inflation:

  L0:  multiplier 1.00, cap 1.10
  L1:  multiplier 1.25, cap 1.35
  L2:  multiplier 1.15, cap 1.25
  L3+: multiplier 1.05, cap 1.15

Why:

  - Residual-vs-implied spread ratios were about L1=1.29, L2=1.22, L3=1.08.
  - KMIA L1 did not justify blanket 1.35x widening.
  - L0 is left alone because HRRR blending and intraday conditioning dominate
    same-day uncertainty.
""")

    print("\nPART 3: RELATED CORRECTIONS")
    print("-" * 100)
    print("""
Use station-local lead days:

  lead_day_for_station(station, valid_date, now_utc)

Use order-level Kalshi fees:

  fee_for_order(price, contracts)

Do not multiply a rounded one-contract fee by contract count.
""")

    print("\nPART 4: VALIDATION COMMANDS")
    print("-" * 100)
    print("""
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q main.py strategy models data dashboard jobs research verification tests
.venv/bin/python research/profile_calibration.py --start-date 2026-04-01 --end-date 2026-05-06
.venv/bin/python research/backtest_variance_fix.py --start 2026-04-01 --end 2026-05-06
.venv/bin/python research/monitor_edge_accuracy.py --hours 1000

Current smoke expectations:

  - Tests: 23 passed
  - Backtest: about +$14 expected-P&L improvement on Apr 1-May 6
  - L1 corrected baseline: about +0.159
""")

    print("\nPART 5: TUNING RULES")
    print("-" * 100)
    print("""
Do not tune from tiny samples. Wait for 30-50 additional settled fills.

If L1 remains overconfident above +0.15:
  - Consider L1 1.30x.
  - Do not change L2/L3 unless their own samples support it.

If L1 becomes underconfident below -0.05:
  - Consider L1 1.20x.

If only one station drifts:
  - Investigate station-specific bias/data first.
  - Avoid global multiplier changes.
""")


if __name__ == "__main__":
    show_variance_fix_report()
