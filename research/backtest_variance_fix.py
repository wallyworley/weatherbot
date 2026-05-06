"""Backtest the variance fix on historical April-May data.

Simulates what P&L would have been if the 1.35x lead_day>=1 variance inflation
had been in place. Compares:
  - PRE-FIX: using original (narrow) distributions
  - POST-FIX: using 1.35x-widened distributions for lead >= 1

Shows: How much P&L improves with the fix applied.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from collections import defaultdict

import numpy as np
from psycopg.rows import dict_row

from weather_bot.data import persistence
from weather_bot.models.distribution import build_cdf_from_percentiles, PiecewiseCDF

log = logging.getLogger(__name__)


def simulate_fair_prob_with_variance_fix(
    station: str,
    target_date: date,
    var: str,
    lower_f: float | None,
    upper_f: float | None,
    apply_fix: bool = True,
) -> tuple[float, float]:
    """Compute fair probability of landing in (lower_f, upper_f).

    Returns: (fair_prob_pre_fix, fair_prob_post_fix)

    The fix only affects lead_day >= 1, so lead_day==0 is identical.
    """
    from weather_bot.data import persistence
    from datetime import datetime, timezone
    from weather_bot.config import STATIONS
    import pytz

    # Reconstruct lead_day (matches distribution.py logic)
    now = datetime.now(tz=timezone.utc)
    lead_day = (target_date - now.date()).days

    rows = persistence.latest_nbm_percentiles(station, target_date, var=var)
    cdf_base = build_cdf_from_percentiles(rows)
    if cdf_base is None:
        return None, None

    month = target_date.month

    # Get bias row
    bias_row = persistence.get_station_bias(station, "NBM_QMD", var, month, max(lead_day, 0))

    if bias_row:
        n = int(bias_row.get("sample_size") or 0)
        raw_bias = float(bias_row["mean_bias_f"])
        raw_std = float(bias_row["stddev_f"])

        # Shrinkage + staleness logic (same as distribution.py)
        _PRIOR_N = 10
        shrink = n / (n + _PRIOR_N) if n > 0 else 0.0
        se_mean = raw_std / (n ** 0.5) if n > 0 else float("inf")
        if abs(raw_bias) < se_mean:
            shrink = 0.0
        staleness = 1.0  # Assume recent data for this backtest

        effective_bias = shrink * raw_bias * staleness

        # PRE-FIX: narrow variance
        cdf_pre = PiecewiseCDF(
            values=cdf_base.values.copy(),
            probs=cdf_base.probs.copy(),
            tail_scale=cdf_base.tail_scale,
        )
        cdf_pre.shift -= effective_bias

        target_std_pre = raw_std
        if target_std_pre > 0 and len(cdf_pre.values) >= 2:
            cur_p90 = float(np.interp(0.90, cdf_pre.probs, cdf_pre.values))
            cur_p10 = float(np.interp(0.10, cdf_pre.probs, cdf_pre.values))
            current_std = (cur_p90 - cur_p10) / 2.56
            if current_std > 0 and target_std_pre > current_std:
                scale_pre = min(target_std_pre / current_std, 1.10)
                median_val = float(np.interp(0.5, cdf_pre.probs, cdf_pre.values))
                cdf_pre.values = median_val + scale_pre * (cdf_pre.values - median_val)

        # POST-FIX: wider variance for lead >= 1
        cdf_post = PiecewiseCDF(
            values=cdf_base.values.copy(),
            probs=cdf_base.probs.copy(),
            tail_scale=cdf_base.tail_scale,
        )
        cdf_post.shift -= effective_bias

        target_std_post = raw_std
        if lead_day >= 1:
            target_std_post *= 1.35

        if target_std_post > 0 and len(cdf_post.values) >= 2:
            cur_p90 = float(np.interp(0.90, cdf_post.probs, cdf_post.values))
            cur_p10 = float(np.interp(0.10, cdf_post.probs, cdf_post.values))
            current_std = (cur_p90 - cur_p10) / 2.56
            if current_std > 0 and target_std_post > current_std:
                max_widen = 1.45 if lead_day >= 1 else 1.10
                scale_post = min(target_std_post / current_std, max_widen)
                median_val = float(np.interp(0.5, cdf_post.probs, cdf_post.values))
                cdf_post.values = median_val + scale_post * (cdf_post.values - median_val)

        fair_pre = cdf_pre.prob_between(lower_f, upper_f)
        fair_post = cdf_post.prob_between(lower_f, upper_f) if apply_fix else fair_pre

        return fair_pre, fair_post

    return None, None


def backtest(start_date: date = None, end_date: date = None):
    """Backtest variance fix on settled paper fills."""
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=60)

    sql = """
    SELECT
        pf.ts::date AS trade_date,
        pf.ts,
        m.station,
        m.var,
        pf.side,
        pf.price,
        pf.contracts,
        m.lower_f, m.upper_f,
        m.valid_date,
        COALESCE(pf.payout, 0) AS payout
    FROM paper_fill pf
    JOIN kalshi_market m ON m.ticker = pf.ticker
    WHERE pf.settled = TRUE
      AND pf.ts::date BETWEEN %(start_date)s AND %(end_date)s
    ORDER BY pf.ts DESC
    """

    results_by_lead = defaultdict(
        lambda: {"pre_fills": 0, "post_fills": 0, "pre_pnl": 0.0, "post_pnl": 0.0}
    )
    total_pre_pnl = 0.0
    total_post_pnl = 0.0
    n_fills = 0

    with persistence.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {"start_date": start_date, "end_date": end_date})
            for row in cur.fetchall():
                n_fills += 1
                lead_day = (row["valid_date"] - row["ts"].date()).days

                # Outcome: 1 if payout=1, 0 if payout=0
                outcome = 1.0 if row["payout"] > 0.5 else 0.0

                # Get fair probabilities with and without fix
                fair_pre, fair_post = simulate_fair_prob_with_variance_fix(
                    row["station"],
                    row["valid_date"],
                    row["var"],
                    row["lower_f"],
                    row["upper_f"],
                    apply_fix=True,
                )

                if fair_pre is None or fair_post is None:
                    continue

                # Expected value: win_prob * (1 - price) - (1 - win_prob) * price - fee
                # For simplicity, estimate fee as ~2% of price
                fee_est = 0.02 * row["price"]

                if row["side"] == "YES":
                    win_prob_pre = fair_pre
                    win_prob_post = fair_post
                else:
                    win_prob_pre = 1.0 - fair_pre
                    win_prob_post = 1.0 - fair_post

                # Realized P&L: payout - price (actual) - fee
                realized_pnl = (row["payout"] - row["price"]) * row[
                    "contracts"
                ] - fee_est * row["contracts"]

                # Expected P&L pre-fix
                ev_pre = (
                    win_prob_pre * (1.0 - row["price"])
                    - (1.0 - win_prob_pre) * row["price"]
                    - fee_est
                ) * row["contracts"]

                # Expected P&L post-fix
                ev_post = (
                    win_prob_post * (1.0 - row["price"])
                    - (1.0 - win_prob_post) * row["price"]
                    - fee_est
                ) * row["contracts"]

                # Calibration error: how wrong were we?
                cal_error_pre = abs(win_prob_pre - outcome)
                cal_error_post = abs(win_prob_post - outcome)
                improvement = cal_error_pre - cal_error_post

                results_by_lead[lead_day]["pre_fills"] += 1
                results_by_lead[lead_day]["post_fills"] += 1
                results_by_lead[lead_day]["pre_pnl"] += ev_pre
                results_by_lead[lead_day]["post_pnl"] += ev_post

                total_pre_pnl += ev_pre
                total_post_pnl += ev_post

                if improvement > 0.05:  # Show biggest improvements
                    log.debug(
                        f"{row['station']} {row['valid_date']} L{lead_day}: "
                        f"fair_pre={fair_pre:.3f} fair_post={fair_post:.3f} "
                        f"price={row['price']:.3f} outcome={outcome} "
                        f"ev_pre={ev_pre:.2f} ev_post={ev_post:.2f} "
                        f"+${ev_post - ev_pre:.2f}"
                    )

    return {
        "total_fills": n_fills,
        "total_pre_pnl": total_pre_pnl,
        "total_post_pnl": total_post_pnl,
        "improvement": total_post_pnl - total_pre_pnl,
        "by_lead": dict(results_by_lead),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--start", type=lambda s: date.fromisoformat(s), default=None
    )
    ap.add_argument("--end", type=lambda s: date.fromisoformat(s), default=None)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    print("\n" + "=" * 80)
    print("BACKTEST: Variance Fix Impact on April 1 - May 6 Data")
    print("=" * 80 + "\n")

    results = backtest(start_date=args.start, end_date=args.end)

    print(f"\nTOTAL FILLS: {results['total_fills']}")
    print(f"\nPRE-FIX (narrow variance):")
    print(f"  Expected P&L: ${results['total_pre_pnl']:+.2f}")
    print(f"\nPOST-FIX (1.35x variance for lead >= 1):")
    print(f"  Expected P&L: ${results['total_post_pnl']:+.2f}")
    print(f"\nIMPROVEMENT:")
    print(f"  ${results['improvement']:+.2f}")
    print(f"  {results['improvement'] / abs(results['total_pre_pnl']) * 100:+.1f}% gain")

    print(f"\n\nBREAKDOWN BY LEAD_DAY:")
    print(f"{'Lead':<6} {'Fills':<8} {'Pre-Fix P&L':<15} {'Post-Fix P&L':<15} {'Improvement':<12}")
    print("-" * 60)
    for lead in sorted(results["by_lead"].keys()):
        r = results["by_lead"][lead]
        print(
            f"L{lead:<5} {r['pre_fills']:<8} "
            f"${r['pre_pnl']:>10.2f}     "
            f"${r['post_pnl']:>10.2f}     "
            f"${r['post_pnl'] - r['pre_pnl']:>+8.2f}"
        )
