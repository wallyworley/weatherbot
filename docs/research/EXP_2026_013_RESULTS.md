# EXP-2026-013 — Shadow-Ensemble Market-Relative Benchmark — Results

_run 2026-06-09 22:40 UTC on the VPS (`research/reports/exp_2026_013_results.md`,
canonical machine artifact). Locked prereg: `EXP_2026_013_ENSEMBLE_MARKET_BENCHMARK.md`._

## VERDICT: NO PASS — no ensemble variant beats the market

All twelve locked variants (center / walk-forward bias-corrected center / member-frequency
dist for WEATHERNEXT2, ECMWF_AIFS_ENS, ECMWF_IFS_ENS, GFS_ENS) have **positive**
market-relative Brier AND RPS at both leads, with paired CIs excluding 0 in the
market-winning direction in every case. Cohort: 298 lead-0 + 280 lead-1 events,
19 stations, 2026-05-10 → 2026-06-08.

Per the locked decision rule (§7): **the "genuinely new models" reopening trigger is
consumed.** The accuracy axis stays closed. No re-runs of these models without a new
pre-registration based on genuinely new data.

## Headline numbers (market-relative; positive = market wins)

| variant (best per model, lead-1) | n | dBrier vs mkt | 95% CI | dRPS vs mkt |
|---|---:|---:|---|---:|
| nbm_only (baseline) | 280 | +0.0231 | [+0.0141, +0.0321] | +0.0335 |
| **WEATHERNEXT2_center_bc** | 182 | **+0.0127** | [+0.0022, +0.0233] | +0.0149 |
| ECMWF_AIFS_ENS_center_bc | 185 | +0.0223 | [+0.0116, +0.0329] | +0.0347 |
| ECMWF_IFS_ENS_center_bc | 185 | +0.0259 | [+0.0151, +0.0368] | +0.0499 |
| GFS_ENS_center_bc | 185 | +0.0318 | [+0.0199, +0.0437] | +0.0487 |

Lead-0: nothing comes close (best +0.0824; nbm_only +0.0942). Raw `_dist` variants are
uniformly the worst (uncalibrated ensemble spread is far too sharp/misplaced vs the market).

## The one notable positive (for the record, not a candidate)

**Bias-corrected WeatherNext 2 at lead-1 is the first variant in the program's history to
beat the NBM baseline with a CI excluding zero**: paired ΔRPS vs nbm_only **−0.0152
[−0.0287, −0.0017]**, ΔBrier −0.0086 [−0.0180, +0.0008] (borderline), 6/19 stations beating
the market (most of any variant). It still **loses to the market** (+0.0127 Brier, CI
excludes 0), so it is not a candidate under the locked bar — but it narrows the lead-1
market gap by ~45% relative to NBM-only.

Honest caveats: the bc cohort is the n=182 subset with ≥5 prior days; one summer month;
raw WN2 is severely handicapped by 6-hourly instantaneous sampling (raw center loses by
+0.1253 — the bias correction is removing a real, large, mostly-constant sampling artifact,
~1.3 °F center MAE penalty).

## If anyone ever reopens this (requires a new prereg + genuinely new data)

The only fact here that points anywhere: WN2's *relative* skill at lead-1 despite 6-hourly
sampling. A future source change that gets **hourly** WN2 (or a successor model) would be
genuinely new data and could be pre-registered fresh. Nothing else in this run supports
further mining.

No production probability, sizing, gating, or execution change. Registry: EXP-2026-013.
