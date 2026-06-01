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
from weather_bot.strategy.ev import fee_per_contract

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


# Divergence buckets matching the 2026-05-29 review's cohorts. The current
# MAX_FAIR_MKT_DIVERGENCE cap is 0.20 (blocks the last two buckets); the old
# cap was 0.50.
_DIV_BUCKETS = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 1.01)]


def _trading_layer(cur, days: int):
    """Per-divergence-bucket realized edge on a COUNTERFACTUAL-HOLD basis.

    Unit is actual fills (real entry prices) joined to the signal that
    triggered them (for fair_prob + market mid at decision time) and to the
    market's true settlement. 'Hold' = settle at expiry, ignoring early exit —
    this is the clean test of whether trading at high divergence actually
    loses money, i.e. whether the 0.20 divergence cap is throwing away edge.
    """
    cur.execute(
        f"""
        SELECT pf.side, pf.price, pf.contracts, pf.fees,
               pf.payout, pf.exit_price, pf.exit_fees,
               km.station, km.lower_f, km.upper_f,
               s.fair_prob, s.market_ask, s.market_bid,
               {_REALIZED_SQL} AS realized
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          LEFT JOIN signal s    ON s.id = pf.signal_id
          LEFT JOIN cli_obs co  ON co.station = km.station AND co.local_date = km.valid_date
         WHERE pf.settled
           AND km.valid_date >= CURRENT_DATE - %s
           AND km.valid_date <  CURRENT_DATE
        """,
        (days,),
    )
    rows = []
    for r in cur.fetchall():
        if r["realized"] is None or r["fair_prob"] is None:
            continue
        if r["market_ask"] is None or r["market_bid"] is None:
            continue
        lo, hi, real = r["lower_f"], r["upper_f"], float(r["realized"])
        yes_win = not ((lo is not None and real < lo) or (hi is not None and real >= hi))
        side_won = (r["side"] == "YES") == yes_win
        price = float(r["price"])
        # counterfactual hold: settle at expiry, entry fee only
        hold_net_pc = (1.0 if side_won else 0.0) - price - fee_per_contract(price)
        # actual realized (with early exit if any), per contract
        if r["exit_price"] is not None:
            actual_pc = (float(r["exit_price"]) - price
                         - (r["fees"] + (r["exit_fees"] or 0)) / r["contracts"])
        else:
            actual_pc = (float(r["payout"]) - price - r["fees"] / r["contracts"])
        mid_yes = (float(r["market_ask"]) + float(r["market_bid"])) / 2.0
        div = abs(float(r["fair_prob"]) - mid_yes)
        rows.append((div, hold_net_pc, actual_pc, side_won, r["station"], r["contracts"]))

    def _bucket(b_lo, b_hi):
        sel = [x for x in rows if b_lo <= x[0] < b_hi]
        if not sel:
            return None
        n = len(sel)
        return dict(
            n=n,
            div=sum(x[0] for x in sel) / n,
            hold=sum(x[1] for x in sel) / n,
            actual=sum(x[2] for x in sel) / n,
            win=sum(1 for x in sel if x[3]) / n,
            contracts=sum(x[5] for x in sel),
        )

    return rows, [(lo, hi, _bucket(lo, hi)) for lo, hi in _DIV_BUCKETS]


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

    # Trading-layer: does high divergence actually lose? (divergence-cap test)
    with persistence.connect() as conn, conn.cursor() as cur:
        tl_rows, tl_buckets = _trading_layer(cur, args.days)
    _hr("TRADING LAYER — realized edge by |fair-mkt| divergence (per contract)")
    print("  Counterfactual HOLD-to-settlement on actual fills. The 0.20 cap "
          "blocks\n  the bottom two buckets; if their HOLD edge is positive, the "
          "cap cuts real edge.")
    print(f"\n  {'divergence':<14}{'n':>5}{'contracts':>10}{'avg_div':>9}"
          f"{'HOLD edge':>11}{'ACTUAL edge':>12}{'win%':>7}")
    for lo, hi, b in tl_buckets:
        rng = f"[{lo:.2f},{hi:.2f})"
        cap = "  <-CAP blocks" if lo >= 0.20 else ""
        if b is None:
            print(f"  {rng:<14}{'0':>5}{'-':>10}{'-':>9}{'-':>11}{'-':>12}{'-':>7}{cap}")
            continue
        print(f"  {rng:<14}{b['n']:>5}{b['contracts']:>10}{b['div']:>9.3f}"
              f"{b['hold']:>+11.4f}{b['actual']:>+12.4f}{100*b['win']:>6.0f}%{cap}")
    print("\n  HOLD edge = if we'd held to settlement (clean signal). ACTUAL = "
          "with early\n  exits (take-profit). HOLD>0 in capped buckets ⇒ the "
          "0.20 divergence cap is\n  too tight and the 05-29 'winner's curse' "
          "read was the contamination talking.")

    # Per-station trading layer: which stations actually extract hold-edge?
    _hr("TRADING LAYER PER-STATION — hold-edge per contract (sorted best→worst)")
    by_st = defaultdict(list)
    for div, hold, actual, won, st, contracts in tl_rows:
        by_st[st].append((hold, actual, won, contracts))
    print(f"  {'station':<9}{'n':>5}{'contracts':>10}{'HOLD edge':>11}"
          f"{'ACTUAL edge':>12}{'win%':>7}")
    st_stats = []
    for st, xs in by_st.items():
        n = len(xs)
        st_stats.append((st, n, sum(x[3] for x in xs),
                         sum(x[0] for x in xs) / n, sum(x[1] for x in xs) / n,
                         sum(1 for x in xs if x[2]) / n))
    for st, n, contracts, hold, actual, win in sorted(st_stats, key=lambda x: -x[3]):
        print(f"  {st:<9}{n:>5}{contracts:>10}{hold:>+11.4f}{actual:>+12.4f}"
              f"{100*win:>6.0f}%")
    print("\n  Positive HOLD edge = genuine model edge vs market. Negative = "
          "the bot's\n  'edge' is noise and it's paying spread/fees/adverse "
          "selection to a better market.")


if __name__ == "__main__":
    main()
