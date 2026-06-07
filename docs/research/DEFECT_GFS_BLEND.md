# Defect Report: GFS Center Blend / Point-in-Time Alignment

**Date:** 2026-06-06
**Defect class:** Blend weight justified by a false (point-in-time-artifact) claim
**Severity:** Medium
**Recommendation:** Demote the constant 0.30 GFS weight to research-only; the
in-code justification is false under strict point-in-time alignment. A small
decorrelation blend *may* survive re-derivation, but only with OOS market-relative
proof.

---

## 1. Files and functions inspected

- `models/distribution.py`
  - GFS blend block, lines 496–513: `_GFS_WEIGHT = 0.30`, applied at `lead_day ≥ 1`
    and at `lead_day == 0` when HRRR is unavailable; `cdf.shift += w*(gfs_val - nbm_median)`
  - the in-code claim (lines 497–499): *"GFS consistently beats NBM at all stations
    (MAE 1.05-1.24°F vs 1.56-2.85°F)"*
- `data/persistence.py` — `latest_det_tmax`, `det_tmax_as_of`, `gfs_tmax_as_of`
  (daily TMAX = `MAX(TMP_2M)` over station-local day, latest run ≤ as_of)
- `research/compare_forecasts.py` — the **origin of the claim**; its own docstring
  warns it used Open-Meteo's historical-forecast-api for GFS, which "doesn't expose
  run_time granularity ... archives the best-available forecast for a target date
  rather than precisely as-issued at lead-1 run."
- Research diagnostic: `research/gfs_nbm_pit_center.py`
- Cross-check: `research/reports/morning_center_ablation_45d_0600_0900_cal.md`

## 2. The defect

The 0.30 GFS weight rests on a claim that **GFS beats NBM on TMAX MAE**. That claim
came from `compare_forecasts.py`, which sourced GFS from Open-Meteo's archived
"best-available" forecast — **not** point-in-time as-issued data — while NBM came
from raw run-time-stamped GRIB. That is precisely the point-in-time / valid-time
aggregation artifact the research charter (EXP-2026-004) flagged: GFS was allowed a
later-issued, more-informed forecast than NBM.

Re-derived under **strict** alignment (same `det_forecast`/`prob_forecast` tables,
`run_time ≤ as_of`, station-local valid-time aggregation, scored at the actual
benchmark event timestamps), the ordering **reverses**.

## 3. Data used and sample size

Lead-0: 291 events; lead-1: 270 events (coherent-snapshot timestamps).
Center pulled with `run_time ≤ snapshot_ts`, vs CLI truth.

**Point-in-time daily-TMAX MAE (°F):**

| lead | NBM p50 | GFS | HRRR | NBM + 0.30·GFS (prod blend) |
|---:|---:|---:|---:|---:|
| 0 | **1.571** (n=291) | 1.815 (n=282) | 2.218 | 1.444 (n=282) |
| 1 | **1.654** (n=270) | 2.176 (n=263) | 4.085 | 1.599 (n=263) |

(GFS station-bias rows were ≈0 in this window, so raw and bias-adjusted GFS MAE are
identical.)

**Market-relative cross-check** (morning 06–09 window, 45d, n=1313;
`morning_center_ablation`): every center variant loses to the market —
`nbm_only` skill −0.30, `station_gfs_50_50` −0.28 (best), `rebuilt_prod` −0.29,
`logged_model` −0.36. No GFS-containing variant reaches positive market skill.

## 4. Metrics used

Mean absolute center error vs CLI truth (point-in-time); market-relative Brier/RPS
skill (cross-check).

## 5. Exact conclusion

**The claim "GFS beats NBM" is false under point-in-time alignment.** With matched
run-time and valid-time aggregation, **NBM p50 beats GFS** at both lead 0 (1.571 vs
1.815) and lead 1 (1.654 vs 2.176), and beats HRRR by more. The original claim was a
source/alignment artifact. The constant 0.30 GFS weight is therefore **not
justified by GFS superiority.**

There is one real but small effect: the **blend** `NBM + 0.30·GFS` lowers center MAE
versus NBM-only (1.571 → 1.444 at lead 0; 1.654 → 1.599 at lead 1) through
error decorrelation, not because GFS is better. That ~3–8% center-MAE gain is **not
enough to beat the market** — the ablation shows GFS-containing centers still at
market skill ≈ −0.28. So the blend is, at best, a minor center-MAE reducer that
does nothing for market-relative skill, and it is currently sold on a false premise.

## 6. Statistical limitations

- GFS in `det_forecast` only spans ~2026-05-01→present, so the comparison is ~5–6
  weeks, one season, ~21 stations.
- HRRR at lead 1 (MAE 4.085) is expected to be poor — HRRR is short-range and is not
  used at lead 1 in production; included only for completeness.
- Daily TMAX via `MAX(hourly TMP_2M)` can over-read transient spikes for the
  deterministic models, inflating their MAE somewhat relative to NBM's native
  daily p50.
- The decorrelation benefit (1.571 → 1.444) is in-sample to this window; it must be
  walk-forward validated before any weight is trusted.

## 7. Overfitting risk

**Medium.** Re-deriving a GFS (or GFS/ECMWF) decorrelation weight is a classic place
to overfit a small window. Use coarse weights, station pooling, walk-forward, and
market-relative validation.

## 8. Recommended next step

1. Correct or delete the false MAE claim in `models/distribution.py`.
2. Re-derive a candidate multi-model center (NBM + GFS [+ ECMWF]) **for decorrelation
   only**, walk-forward, and score on `snapshot_market_benchmark.py` vs market.
3. Keep the production GFS weight **frozen/demoted** until a re-derived blend shows
   OOS improvement in market-relative Brier/RPS (`WEATHERBOT_PROMOTION_CRITERIA.md`
   §2). Center-MAE improvement alone is **not** sufficient for promotion.
