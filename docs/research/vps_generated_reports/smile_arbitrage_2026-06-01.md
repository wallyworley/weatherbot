# Smile Arbitrage Scan — 2026-06-01 22:55 UTC

Scanned active/open events for active trade stations (`KNYC, KMDW, KMIA, KPHX, KLAS, KMSY, KDCA, KSFO, KDFW, KATL, KPHL, KOKC, KLAX, KDEN, KAUS, KSAT, KBOS, KSEA, KMSP`). Flagged events have at least one bucket whose normalized-share residual exceeds `0.03` with snapshot age ≤ `30` min and non-zero depth on the actionable side.

**Method.** `over_round = Σ_i yes_mid_i`. The buckets in an event partition the outcome, so the true sum must be `1.0`. Per-bucket `normalized_share - yes_mid` measures algebraic mispricing — positive means YES is too cheap (renormalized), negative means YES is too rich. Edge is *independent of forecast skill* but settlement risk and execution risk (fees, depth, snapshot staleness) still apply.

_No events flagged._