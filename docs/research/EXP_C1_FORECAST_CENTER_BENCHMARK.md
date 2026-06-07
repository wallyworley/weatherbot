# EXP-C1 — Forecast-Center Market-Relative Benchmark (first pass)

**Date:** 2026-06-06
**Experiment:** EXP-2026-007 / EXP-C1 (charter Q3 — the program's binding question)
**Status:** First pass complete (parameter-free centers). **IN-SAMPLE / pre-calibrator.**
**Headline:** **No available forecast center beats the market-implied center** at either
lead. NBM is already the best simple center; the market wins universally.

This also satisfies Priority-Backlog Story 4.1 (forecast-center market-relative benchmark).

---

## 1. Question

Charter Q3: *can any forecast center beat the market-implied center out of sample?* B1–B3
showed mechanical center *tweaks* don't create edge; C1 asks whether any *center* does —
including ones the bot doesn't use as a primary center (ECMWF, a multi-model
decorrelation blend).

## 2. Files / functions / data

- `research/center_market_benchmark.py` (built for this experiment; research-only).
  - `_process_event` — rebuilds the NBM-only distribution point-in-time
    (`build_station_distribution(..., as_of=ts, center_blend_weights={"NBM":1.0})`), then
    sets `cdf.shift = base + (candidate_center − nbm_median)` (NBM shape kept) and scores
    each candidate against the same market mids via `score_event` (canonical scoring).
  - `_candidate_centers`, `_agg`, `_paired_vs_nbm`.
- Centers: NBM p50 (bias-corrected) from `prob_forecast`; GFS / ECMWF / HRRR daily TMAX
  (raw) from `det_forecast` via `gfs_tmax_as_of` / `det_tmax_as_of` / `hrrr_tmax_as_of`.
- Events: coherent-snapshot benchmark events, lead 0–1, stored market midpoints,
  CLI/`expiration_value` settlement.

## 3. Method

Parameter-free → no fitting → no walk-forward needed (fixed centers). Each candidate
center is scored with NBM's spread, point-in-time, vs the market. Negative
`dX_vs_mkt` = center beats market. Pass bar: negative market-relative Brier **and** RPS
with paired CI excluding 0.

## 4. Sample size

Lead-0 = 297 events; lead-1 = 276 (per-center n lower where a model is missing as-of:
ECMWF 244–262, GFS 269–286, HRRR/NBM full). ~6-week window, ~21 stations.

## 5. Results

### Lead 1 — market-relative (positive = market wins)

| center | n | dBrier_vs_mkt | dBrier 95% CI | dRPS_vs_mkt | dRPS 95% CI | dCenterMAE |
|---|---:|---:|---|---:|---|---:|
| **nbm_only** (best) | 276 | **+0.0231** | [+0.0145, +0.0316] | **+0.0336** | [+0.0226, +0.0446] | +0.13 |
| gfs_center | 269 | +0.0405 | [+0.0308, +0.0502] | +0.0704 | [+0.0555, +0.0854] | +0.50 |
| ecmwf_center | 244 | +0.0737 | [+0.0606, +0.0869] | +0.1389 | [+0.1158, +0.1619] | +1.00 |
| hrrr_center | 276 | +0.0728 | [+0.0605, +0.0852] | +0.1248 | [+0.1037, +0.1458] | +0.80 |
| blend_nge | 276 | +0.0273 | [+0.0190, +0.0356] | +0.0478 | [+0.0361, +0.0595] | +0.26 |

Paired vs nbm_only (positive = worse than NBM): gfs +0.0172 [+0.0088,+0.0256];
ecmwf +0.0510; hrrr +0.0498; **blend +0.0042 Brier [−0.0009,+0.0093]** (≈NBM on Brier,
worse on RPS). → **No center beats NBM at lead-1.**

### Lead 0 — market-relative (positive = market wins)

| center | n | dBrier_vs_mkt | dRPS_vs_mkt | dCenterMAE |
|---|---:|---:|---:|---:|
| nbm_only | 295 | +0.1082 | +0.1074 | +0.70 |
| gfs_center | 286 | +0.1025 | +0.1030 | +0.68 |
| ecmwf_center | 262 | +0.1195 | +0.1287 | +0.84 |
| **hrrr_center** (best) | 295 | **+0.0942** | +0.0871 | +0.54 |
| blend_nge | 295 | +0.1030 | +0.1053 | +0.69 |

Paired vs nbm_only at lead-0: **hrrr −0.0140 Brier [−0.0283,+0.0004], −0.0203 RPS
[−0.0344,−0.0062]** (HRRR center helps at lead-0, consistent with EXP-B2); gfs/blend ≈ NBM
(CI includes 0); ecmwf worse. But **all still lose to the market** by a wide margin.

## 6. Exact conclusion

**No available parameter-free forecast center beats the market-implied center** at lead 0
or lead 1. Every candidate — NBM, GFS, ECMWF, HRRR, and a NBM/GFS/ECMWF decorrelation
blend — has **positive** market-relative Brier **and** RPS at both leads (CIs exclude 0
in the wrong direction). NBM-only is already the best center at lead-1; HRRR is best at
lead-0 (and beats NBM there, consistent with the HRRR-blend result) but still loses to the
market by +0.094 Brier. The multi-model decorrelation blend does not beat NBM. This is the
clearest statement yet of the program's core finding: **the deficit is forecast
information/center resolution, and the readily-available centers do not contain it.**

## 7. Statistical limitations

- **In-sample to the existing ~6-week window** (parameter-free, so no fitting, but not
  "fresh OOS station-days" in the kill-rule sense).
- **Deterministic centers are raw** — `station_bias` has only NBM rows, so GFS/ECMWF/HRRR
  are un-bias-corrected while NBM is corrected. A systematic deterministic bias could flatter
  NBM; however the lead-1 gaps to market (+0.04 to +0.07) are far larger than plausible bias
  shifts, and ECMWF/HRRR also lose at lead-0. Bias-correction is unlikely to flip the result
  but is the right next control (EXP-C1b).
- Centers tested with **NBM's spread** (isolates the *center* question); a different model's
  native spread could score differently (separate question).
- Per-center n varies with model availability; ECMWF window is shortest (~from 2026-05-10).

## 8. Overfitting risk

**Low** — no parameters were fit. The risk is the opposite (these fixed centers may be
*under*-tuned, esp. raw deterministic ones); that is what EXP-C1b addresses.

## 9. Recommended next step (EXP-C1b — the remaining hope, walk-forward)

The parameter-free centers are exhausted with a clear negative. The remaining,
harder-to-overfit candidates require **walk-forward** training on prior days only:

1. **Bias-corrected deterministic centers** (GFS/ECMWF/HRRR minus a walk-forward trailing
   bias) — removes the raw-center limitation.
2. **Inverse-recent-MAE decorrelation weights** (NBM/GFS/ECMWF), walk-forward.
3. **Regime-conditioned** center weights (by station residual regime / lead / season).
4. **CLI-obs-anchored** centers (lead-0).

Each scored on the canonical benchmark, market-relative, **walk-forward**, with the
pass bar of §3 and ≥100 fresh station-days. Given the size and consistency of the gaps
here and in B1–B3, the realistic prior is that the **program kill rule** (charter §7: no
center beats market-relative RPS+Brier after 500 fresh station-days or 90 days →
observation-only) will be approached. EXP-C1b is the decisive test before that call.
