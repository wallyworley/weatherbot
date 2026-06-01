"""
Mark-to-settlement calibration — the UNCONTAMINATED overconfidence signal.

Why this exists
---------------
`jobs/paper_report.py`'s EXPECTED-vs-REALIZED edge check only looks at
held-to-expiry fills (`exit_price IS NULL`). Since take-profit (0.70) skims
essentially every winner off early, that bucket is ~0% win *by construction*,
so it reports massive "overconfidence" that is really exit-timing adverse
selection — not a property of the model. The 2026-05-29 calibration change was
built on that artifact.

This tool measures calibration the right way: for EVERY signal (every bucket we
priced, not just the ones we filled), compare the model's P(YES) `fair_prob`
against the market's TRUE settled outcome — independent of whether/when we
exited. `fair_prob` is always P(YES) for the bucket (ev.py), so calibration is
side-independent: P(YES) vs "did the realized high land in [lower_f, upper_f)".

Outcome source mirrors settle_paper_fills priority: Kalshi `expiration_value`,
then NWS `cli_obs` (never METAR — it undercounts CLI by ~1F and mis-settles
boundary buckets).

Reports, per station and overall:
  * n, base rate (YES freq), mean predicted P(YES), bias
  * Brier score + Brier skill score vs the climatology base-rate reference
  * Expected Calibration Error (ECE) + a reliability table
  * Cohort comparison: ALL signals vs FILLED vs HELD-to-expiry — this is the
    direct proof of the contamination.

Usage:
    python -m weather_bot.research.calibration_mark_to_settlement
    python -m weather_bot.research.calibration_mark_to_settlement --days 60
    python -m weather_bot.research.calibration_mark_to_settlement --lead 0
    python -m weather_bot.research.calibration_mark_to_settlement --bins 10 --signal-pick last
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from weather_bot.data import persistence

# Realized settlement value for a market: Kalshi's expiration_value first
# (what we'd actually be paid against), else NWS CLI tmax/tmin. NULL => the day
# hasn't resolved for us yet, so the market is excluded.
_REALIZED_SQL = """
COALESCE(
    NULLIF(km.payload->>'expiration_value','')::float,
    CASE WHEN km.var = 'TMAX_DAILY' THEN co.tmax_f
         WHEN km.var = 'TMIN_DAILY' THEN co.tmin_f END
)
"""

# YES wins iff lower_f <= realized < upper_f (NULL bound = +/-inf). Mirrors
# settle_paper_fills._yes_wins exactly.
_YES_WIN_SQL = """
CASE
    WHEN {realized} IS NULL THEN NULL
    WHEN km.lower_f IS NOT NULL AND {realized} <  km.lower_f THEN 0
    WHEN km.upper_f IS NOT NULL AND {realized} >= km.upper_f THEN 0
    ELSE 1
END
""".format(realized=_REALIZED_SQL)


def _fetch(cur, days: int, lead: int | None, signal_pick: str):
    """One row per settled bucket-market: station, lead, fair_prob (P(YES)),
    yes_win (0/1), and cohort flags (filled / held-to-expiry)."""
    order = "DESC" if signal_pick == "last" else "ASC"
    # Bound the signal scan: a market for valid_date D is priced from ~D-3..D.
    cur.execute(
        f"""
        WITH mkt AS (
            SELECT km.ticker, km.station, km.valid_date,
                   {_YES_WIN_SQL} AS yes_win
              FROM kalshi_market km
              LEFT JOIN cli_obs co
                     ON co.station = km.station AND co.local_date = km.valid_date
             WHERE km.valid_date >= CURRENT_DATE - %s
               AND km.valid_date <  CURRENT_DATE
        ),
        sig AS (
            SELECT DISTINCT ON (s.ticker)
                   s.ticker, s.fair_prob, s.ts, s.action
              FROM signal s
             WHERE s.ts >= CURRENT_DATE - (%s + 4)
               AND s.fair_prob IS NOT NULL
             ORDER BY s.ticker, s.ts {order}
        ),
        fills AS (
            SELECT pf.ticker,
                   bool_or(TRUE)                                            AS filled,
                   bool_or(pf.payout IS NOT NULL AND pf.exit_price IS NULL) AS held_expiry
              FROM paper_fill pf
             GROUP BY pf.ticker
        )
        SELECT m.station,
               (m.valid_date - sig.ts::date)        AS lead,
               sig.fair_prob::float                 AS p_yes,
               m.yes_win::int                       AS yes_win,
               COALESCE(f.filled, FALSE)            AS filled,
               COALESCE(f.held_expiry, FALSE)       AS held_expiry
          FROM mkt m
          JOIN sig             ON sig.ticker = m.ticker
          LEFT JOIN fills f    ON f.ticker   = m.ticker
         WHERE m.yes_win IS NOT NULL
        """,
        (days, days),
    )
    rows = cur.fetchall()
    if lead is not None:
        rows = [r for r in rows if r["lead"] == lead]
    return rows


def _metrics(rows, bins: int):
    """Brier, BSS, ECE, base rate, mean prediction, and reliability bins."""
    n = len(rows)
    if n == 0:
        return None
    base = sum(r["yes_win"] for r in rows) / n          # observed YES freq
    pred = sum(r["p_yes"] for r in rows) / n            # mean predicted P(YES)
    brier = sum((r["p_yes"] - r["yes_win"]) ** 2 for r in rows) / n
    ref = base * (1.0 - base)                            # climatology Brier
    bss = (1.0 - brier / ref) if ref > 0 else float("nan")

    # reliability bins
    buckets = defaultdict(lambda: [0, 0.0, 0.0])  # count, sum_pred, sum_out
    for r in rows:
        b = min(bins - 1, int(r["p_yes"] * bins))
        buckets[b][0] += 1
        buckets[b][1] += r["p_yes"]
        buckets[b][2] += r["yes_win"]
    ece = 0.0
    reliability = []
    for b in range(bins):
        cnt, sp, so = buckets[b]
        if cnt == 0:
            continue
        mp, mo = sp / cnt, so / cnt
        ece += (cnt / n) * abs(mp - mo)
        reliability.append((b / bins, (b + 1) / bins, cnt, mp, mo))
    return dict(n=n, base=base, pred=pred, brier=brier, bss=bss, ece=ece,
                reliability=reliability)


def _hr(title: str):
    print("\n" + "=" * 76 + f"\n{title}\n" + "=" * 76)


def _fmt_row(label, m):
    arrow = ""
    if m["pred"] - m["base"] > 0.02:
        arrow = "  <- predicts YES too often (overconfident-high)"
    elif m["base"] - m["pred"] > 0.02:
        arrow = "  <- predicts YES too rarely"
    return (f"  {label:<10} n={m['n']:>5}  base={m['base']:.3f}  pred={m['pred']:.3f}  "
            f"bias={m['pred']-m['base']:+.3f}  Brier={m['brier']:.4f}  "
            f"BSS={m['bss']:+.3f}  ECE={m['ece']:.3f}{arrow}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--lead", type=int, default=None,
                    help="restrict to a single lead day (0 = same-day, what we trade)")
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--signal-pick", choices=("last", "first"), default="last",
                    help="which signal per ticker (last=most-informed forecast)")
    args = ap.parse_args()

    with persistence.connect() as conn, conn.cursor() as cur:
        rows = _fetch(cur, args.days, args.lead, args.signal_pick)

    lead_txt = "all leads" if args.lead is None else f"lead={args.lead}"
    _hr(f"MARK-TO-SETTLEMENT CALIBRATION — last {args.days}d, {lead_txt}, "
        f"signal={args.signal_pick}")
    overall = _metrics(rows, args.bins)
    if overall is None:
        print("  (no settled markets with signals in window)")
        return
    print(_fmt_row("OVERALL", overall))
    print("  (BSS>0 = beats climatology; ECE=0 = perfectly calibrated; "
          "bias>0 = model says YES more than it happens)")

    # Per-station
    _hr("PER-STATION (sorted by ECE, worst-calibrated first)")
    by_st = defaultdict(list)
    for r in rows:
        by_st[r["station"]].append(r)
    st_metrics = [(st, _metrics(rs, args.bins)) for st, rs in by_st.items()]
    st_metrics = [x for x in st_metrics if x[1] and x[1]["n"] >= 10]
    for st, m in sorted(st_metrics, key=lambda x: -x[1]["ece"]):
        print(_fmt_row(st, m))

    # Reliability table (overall)
    _hr("RELIABILITY (overall) — predicted P(YES) vs observed YES freq")
    print(f"  {'bin':<12}{'n':>6}{'pred':>8}{'observed':>10}{'gap':>8}")
    for lo, hi, cnt, mp, mo in overall["reliability"]:
        flag = "  **" if abs(mp - mo) > 0.10 else ""
        print(f"  [{lo:.1f},{hi:.1f})    {cnt:>6}{mp:>8.3f}{mo:>10.3f}{mp-mo:>+8.3f}{flag}")

    # Cohort comparison — THE contamination proof
    _hr("COHORT COMPARISON — why paper_report's check is contaminated")
    all_m = _metrics(rows, args.bins)
    filled = _metrics([r for r in rows if r["filled"]], args.bins)
    held = _metrics([r for r in rows if r["held_expiry"]], args.bins)
    print(_fmt_row("ALL", all_m))
    if filled:
        print(_fmt_row("FILLED", filled))
    if held:
        print(_fmt_row("HELD-EXP", held))
    print("\n  If HELD-EXP looks far worse-calibrated than ALL, that is the "
          "take-profit\n  adverse-selection artifact — not model overconfidence. "
          "Trust ALL.")


if __name__ == "__main__":
    main()
