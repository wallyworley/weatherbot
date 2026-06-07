# Defect Report: GFS Center Blend / Point-in-Time Alignment

**Date:** 2026-06-06 · **Updated 2026-06-06 after EXP-B3 (see §9)**
**Defect class:** False *justification* (point-in-time artifact) for an otherwise OK blend
**Severity:** Low (downgraded from Medium after EXP-B3; the false claim is already
corrected in code)
**Recommendation (revised):** **Keep the 0.30 GFS blend.** EXP-B3 shows a *partial*
GFS blend improves market-relative scores vs NBM-only at lead-1 (decorrelation), and
0.30 is near-optimal. The original in-code "GFS beats NBM" justification was false and
is corrected; the **decision** (keep a modest GFS weight) stands on different grounds.
Do not demote.

> ⚠️ **Correction (same pattern as the HRRR report).** Sections 1–8 were written from a
> standalone *point-in-time center-MAE* comparison (NBM beats GFS) and recommended
> *demoting* the blend. **EXP-B3 (§9), distribution-level market-relative scoring — the
> correct test — refutes the demotion:** a partial GFS blend (≈0.15–0.30) beats NBM-only
> at lead-1 via error decorrelation, while *full* GFS (w=1.0) is much worse (consistent
> with GFS's worse standalone MAE). Read §9 as authoritative; §3's MAE table is true but
> incomplete (standalone ≠ blended value).

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

> ⚠️ **Partly SUPERSEDED by §9 (EXP-B3).** The "GFS beats NBM standalone is false"
> finding holds. But this section's claim that the blend "does nothing for
> market-relative skill" is **wrong**: EXP-B3 shows the 0.30 blend *modestly improves*
> market-relative Brier/RPS/CRPS/center vs NBM-only at lead-1. Keep the blend; see §9.

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

1. Correct or delete the false MAE claim in `models/distribution.py`. **(done 2026-06-06)**
2. Re-derive a candidate multi-model center (NBM + GFS [+ ECMWF]) **for decorrelation
   only**, walk-forward, and score on `snapshot_market_benchmark.py` vs market.
   **(GFS weight run as EXP-B3, §9; ECMWF deferred.)**
3. Keep the production GFS weight **frozen** until a re-derived blend shows
   OOS improvement in market-relative Brier/RPS (`WEATHERBOT_PROMOTION_CRITERIA.md`
   §2). Center-MAE improvement alone is **not** sufficient for promotion.

## 9. EXP-B3 experiment results (2026-06-06) — IN-SAMPLE DIAGNOSTIC; supersedes the §5 "no market skill" claim

> ⚠️ In-sample diagnostic, pre-calibrator. No production change. Any weight change needs
> production-like re-score + walk-forward OOS before a production decision.

Harness: `research/gfs_blend_experiment.py` (research-only). Each event's distribution
is rebuilt point-in-time with an **NBM-only center** (`center_blend_weights={"NBM":1.0}`;
bias retained; lead-0 keeps the floor; lead-1 has none; pre-calibrator), then the GFS
shift `w*(gfs_d − nbm_median)` is re-applied for a weight sweep (GFS bias-adjusted with
the event lead) and scored vs the **same** market mids on the canonical benchmark.
**Lead-1 is the primary GFS regime** (lead-0 GFS is only a fallback when HRRR is absent).
`vs_prod` = dBrier − dBrier(prod_0.30); negative = smaller market gap.

**Lead 1 (primary; n=270, GFS available 263):**

| policy (GFS w) | model Brier | dBrier_vs_mkt | dRPS_vs_mkt | dCRPS_vs_mkt | dCenterMAE_vs_mkt | vs_prod |
|---|---:|---:|---:|---:|---:|---:|
| gfs_off (w=0) | 0.1443 | +0.0250 | +0.0361 | +0.151 | +0.15 | **+0.0015 (worse)** |
| gfs_0.15 | 0.1428 | +0.0235 | +0.0342 | +0.133 | +0.13 | +0.0000 |
| **prod_0.30** (current) | 0.1428 | +0.0235 | +0.0348 | +0.138 | +0.13 | +0.0000 |
| gfs_0.50 | 0.1445 | +0.0252 | +0.0396 | +0.180 | +0.19 | +0.0017 |
| gfs_1.00 | 0.1593 | +0.0400 | +0.0676 | +0.429 | +0.48 | +0.0165 |

**Lead 0 (GFS fallback only; n=289):** gfs_off +0.1068 → prod_0.30 +0.0985 → gfs_0.50
**+0.0932** (best) → gfs_1.00 +0.1028. (At lead-0 the production path is HRRR, not GFS;
this regime is covered by EXP-B2. `gfs_off` Brier 0.1943 matches EXP-B2's `w0_nbm` — a
consistency check.)

**Findings:**

1. **The GFS blend is net-helpful via decorrelation — same lesson as HRRR (EXP-B2).**
   At lead-1, `prod_0.30` beats `gfs_off` (NBM-only) on **every** metric (dBrier +0.0235
   vs +0.0250; RPS, CRPS, center all better). So the blend helps despite GFS's worse
   *standalone* MAE — refuting §5's "does nothing for market-relative skill".
2. **0.30 is near-optimal; the curve is concave in weight.** `gfs_0.15` ties `prod_0.30`
   (marginally better CRPS/center), `gfs_0.50` is worse, and **`gfs_1.00` (full GFS
   center) is much worse** (+0.0400) — consistent with GFS standalone < NBM. The benefit
   comes from a *partial* blend, not from GFS being a better center.
3. **The defect was the false justification, not the blend.** The "GFS beats NBM" claim
   was false (corrected in code); the *decision* to carry a modest GFS weight is
   independently supported by decorrelation.
4. **Small, and no edge.** The lead-1 market gap is only +0.0235 (the model is close to
   the market at lead-1), and the blend trims it by ~0.0015 Brier — the market still
   wins. Marginal damage-reduction, not edge.

**Recommendation (revised):** **Keep the 0.30 GFS blend** (do not demote). It is a small
but consistent market-relative improvement over NBM-only at lead-1, and 0.30 is
near-optimal. A weight change is **not** warranted (0.15 and 0.30 are tied within noise;
CIs overlap heavily). If ever revisited: research flag, production-like re-score, and
walk-forward OOS, no in-sample weight tuning. ECMWF decorrelation (existing
`det_forecast` data) is a possible research-only follow-on.

**Statistical limitations:** ~6 weeks, lead-1 n=270; pre-calibrator; weight differences
(0.15 vs 0.30) are within overlapping CIs — treat 0.30 as fine, not precisely optimal.

**Overfitting risk:** Low for "keep a partial GFS blend / full GFS loses". Medium for any
fitted/again-tuned weight.
