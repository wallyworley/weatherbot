# EXP-2026-014 — Kalshi Market Self-Calibration (Favorite-Longshot Bias) — Results

_design-set run 2026-06-09 23:39 UTC on the VPS (`research/reports/exp_2026_014_results.md`,
canonical machine artifact). Locked prereg: `EXP_2026_014_MARKET_SELF_CALIBRATION.md`._

## VERDICT: DESIGN FAIL — the bias exists but is not capturable at executable prices

The locked primary rule (buy 1 YES of the highest-mid bucket at the **ask** iff mid ≥ 0.50,
14–16 UTC reference snapshot, taker fee, hold to settlement) on the full design set
(through 2026-06-08, 1,968 scored bucket rows, 195 qualifying events):

| criterion (prereg §7) | required | observed | pass? |
|---|---|---|---|
| net EV/contract, cluster CI excluding 0 | yes | **+0.0083** [−0.0564, +0.0753] | **NO** |
| both chronological halves positive | yes | +0.0700 / **−0.0043** | **NO** |
| ≥60% of stations (≥10 events) positive | yes | 4/7 = **57%** | **NO** |

Zero of three criteria met. Per the locked decision rule: **the market-self-calibration
axis closes.** No forward window opens; nothing trades.

## What the data actually shows (reference, not a decision input)

The favorite-longshot *shape* is real in this sample but small and cost-dominated:

- Longshots are overpriced: the 0.2–0.3 mid decile wins 18.8% vs 24.8% priced
  (edge −0.0602, cluster CI [−0.1169, −0.0034] — the only decile whose CI excludes 0).
- Favorites' point estimates are positive across every decile ≥ 0.5 (+0.018 to +0.137)
  but individually insignificant (n = 115/48/23/3/6).
- The cost ladder kills it: mid-fill no-fee +0.0408 → ask-fill no-fee +0.0277 → ask-fill
  with taker fee **+0.0083**. Roughly 80% of the raw bias is consumed by spread + fees.
- The symmetric longshot NO side nets +0.0086 after costs — same story.
- The earlier exploratory peek's +5–7pp favorite edge was a mid-price, fee-free artifact
  on a smaller slice; the executable, fee-adjusted, cluster-robust version is ~+0.8¢ ± 6.6¢.

## Interpretation

Kalshi's morning weather prices carry a mild, classic favorite-longshot bias — visible,
sign-consistent with the literature, and **too small to clear retail taking costs** at this
liquidity. A maker-side (post at bid, earn the spread instead of paying it) version is the
only conceivable rescue, but that is execution research on a closed trading program and is
explicitly out of scope; it would need its own prereg, live-order infrastructure, and
adverse-selection measurement (the same sharps who beat our forecasts would be picking off
our quotes).

No production change. Registry: EXP-2026-014.
