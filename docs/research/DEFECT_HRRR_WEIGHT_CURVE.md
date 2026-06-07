# Defect Report: HRRR Late-Day Weight Curve

**Date:** 2026-06-06 · **Updated 2026-06-06 after EXP-B2 (see §9)**
**Defect class:** Mildly mis-tuned blend weight (NOT "blend on an inferior model")
**Severity:** Low (downgraded from Medium–High after EXP-B2)
**Recommendation (revised):** Keep the HRRR blend — turning it off is *worse*. The
curve is only modestly too aggressive in the 13–16h window; a lower mid-afternoon
weight (`cap≈0.50`) is a marginal in-sample **Brier/RPS** damage-reduction candidate
(not a distributional win — CRPS ~flat-to-worse; `flat_0.30` rejected). Do not demote
or disable the blend.

> ⚠️ **Correction.** Sections 1–8 below were written from a point-in-time
> *center-MAE* comparison (HRRR daily-max MAE > NBM at 13–16h) and inferred the blend
> hurts and that `w=0` would help. **EXP-B2 (§9), which scores the full distribution
> market-relative — the correct test — refutes that inference:** HRRR blending
> *improves* market-relative Brier/RPS/CRPS/center vs NBM-only (the point-MAE
> comparison missed the error-decorrelation benefit of a partial blend). Read §9 as
> the authoritative conclusion; treat §3's MAE table as a true but *incomplete* input.

---

## 1. Files and functions inspected

- `models/distribution.py`
  - `_hrrr_blend_weight(hour_local)` — the curve: 0.2 @06h → 0.6 @10h → 0.9 @15h → 0.95 @18h+
  - `build_station_distribution(...)` lines 478–494 — same-day HRRR center shift
    `cdf.shift += w * (hrrr_val - nbm_median)`
  - `latest_hrrr_tmax` / `hrrr_tmax_as_of` (daily TMAX = `MAX(TMP_2M)` over station-local day, latest run)
- Research diagnostics: `research/gfs_nbm_pit_center.py`, ad-hoc by-hour HRRR/NBM MAE
- Cross-check: `research/reports/morning_center_ablation_45d_0600_0900_cal.md`

## 2. The defect

The curve was set on a **single anecdote**. The code comment justifies raising the
weight ("Prior curve ... proved too timid — in paper trading for KNYC 2026-04-19,
HRRR projected 49.6°F ... at 0.33 weight the blended distribution still placed
p50=60.4°F") and then pushes HRRR weight to **0.9 at 15:00** and **0.95 by 18:00**.
There is no multi-day, multi-station validation behind the curve, and the registry
itself flags this experiment **overfitting risk: High**.

When evaluated point-in-time, HRRR's daily-TMAX center is **less accurate than NBM**,
and the curve places near-total weight on it exactly at the hours where most
same-day trading occurs.

## 3. Data used and sample size

Lead-0 benchmark events (coherent snapshot), HRRR/NBM daily-TMAX pulled with
`run_time ≤ snapshot_ts`, station-local valid-time aggregation, vs CLI truth.

**Point-in-time daily-TMAX MAE (lead 0, all snapshots):**

| center | n | MAE °F |
|---|---|---|
| NBM p50 | 291 | **1.571** |
| HRRR | 291 | 2.218 |

**By local hour of the snapshot** (the hour determines the production weight):

| local hour | events | NBM MAE | HRRR MAE | prod HRRR weight |
|---:|---:|---:|---:|---:|
| 12 | 14 | 1.70 | 1.92 | 0.72 |
| 13 | 50 | **1.43** | 2.33 | 0.78 |
| 14 | 46 | **1.35** | 3.01 | 0.84 |
| 15 | 79 | **1.55** | 2.20 | 0.90 |
| 16 | 63 | **1.49** | 1.80 | 0.92 |
| 17 | 13 | 2.10 | 1.40 | 0.93 |
| 18 | 6 | 2.35 | 1.32 | 0.95 |

Cross-check (morning window 06:00–09:00, 45d, n=1313):
`rebuilt_prod` (with HRRR/GFS blend) Brier 0.1327 ≈ `nbm_only` 0.1328; both market
skill ≈ −0.30. Morning HRRR adds nothing.

## 4. Metrics used

Mean absolute error of the model center vs CLI truth, overall and by local hour;
market-relative Brier/RPS skill from the ablation cross-check.

## 5. Exact conclusion

> ⚠️ **SUPERSEDED by §9 (EXP-B2).** This conclusion was inferred from center-MAE and
> is **wrong on the key claim**: distribution-level market-relative scoring shows the
> HRRR blend *helps* (NBM-only is worse), so the blend does not "drag the center
> toward an inferior model." The MAE facts below are correct; the inference is not.

The HRRR center blend is **misweighted against the evidence**. At the peak-heating
hours 13:00–16:00 — which hold **238 of the lead-0 events** — the curve weights
HRRR **0.78–0.92** while HRRR's daily-TMAX MAE (1.8–3.0 °F) is **worse than
NBM's** (1.35–1.55 °F). HRRR only becomes more accurate than NBM after ~17:00,
where few events remain and the day's high is already locked in (and captured by
the intraday floor anyway). The blend therefore drags the center toward the
inferior model during the window that matters most, manufacturing confident-wrong
same-day distributions — consistent with the lead-0 deficit in the benchmark. In
the morning window the blend is simply inert (≈ NBM-only). **There is no hour at
which the current weight is supported by HRRR out-accurate-ing NBM with meaningful
sample.**

## 6. Statistical limitations

- Per-hour bins are small (n = 6–79); the 13:00–16:00 conclusion rests on the
  larger bins (n ≥ 46) and is consistent across all four.
- HRRR daily TMAX is extracted as `MAX(hourly TMP_2M)`, which can over-read transient
  hourly spikes; part of HRRR's apparent error is this aggregation choice (itself a
  reason not to trust HRRR as a hard center).
- "As-of snapshot" HRRR may be a few hours stale relative to the absolute latest run;
  a true issuance-time-by-lead study would sharpen the curve.
- The morning cross-check is a different (06–09) window; it bounds the morning regime
  only.

## 7. Overfitting risk

The **current curve is itself the overfit** (n=1 anecdote). Any re-derivation is
**High** risk: a by-hour weight fit on ~6 weeks will chase noise. Mitigate with
coarse hour bands, station pooling, shrinkage, walk-forward, and market-relative
validation.

## 8. Recommended next step

> ⚠️ **SUPERSEDED by §9 (EXP-B2).** Step 3 below ("test `w=0` as the leading
> damage-reduction candidate") was executed and **`w=0` lost** — keep the blend. Read
> §9 for the current recommendation; the steps below are the original pre-EXP-B2 plan.

1. Build a research-only by-hour, by-lead HRRR-vs-NBM center study (issuance-time
   correct), and a candidate weight curve (including **w=0** as the null).
2. Score candidates on `snapshot_market_benchmark.py` (Brier/RPS/CRPS/center MAE
   vs market), lead-0, out of sample, by hour band.
3. Until then, treat the production curve as **unsupported**: a research flag to set
   HRRR weight to 0 (NBM/GFS-decorrelation center only) should be tested as the
   leading damage-reduction candidate. Promote nothing without OOS market-relative
   evidence (`WEATHERBOT_PROMOTION_CRITERIA.md` §2/§4).

> Step 3's "test w=0" was executed as EXP-B2 (§9) and **w=0 lost** — see below.

## 9. EXP-B2 experiment results (2026-06-06) — IN-SAMPLE DIAGNOSTIC; supersedes §3/§5 inference

> ⚠️ In-sample diagnostic, pre-calibrator. No production change. Any candidate needs
> production-like re-score + walk-forward OOS validation before a production decision.

Harness: `research/hrrr_weight_experiment.py` (research-only). Each lead-0 TMAX
distribution is rebuilt point-in-time with an **NBM-only center**
(`center_blend_weights={"NBM":1.0}`; bias + intraday floor retained; pre-calibrator),
then the HRRR/GFS center shift is re-applied under several weight policies (production
formula `shift += w*(hrrr−nbm_median)`, GFS fallback 0.30), and each is scored against
the **same** market midpoints with the canonical benchmark — overall and by local-hour
band. n = 289 scored of 291 groups; HRRR available as-of in all 291. `prod_curve`
Brier 0.1765 reproduces the canonical lead-0 baseline.

**Overall (negative = better; `vs_prod` = dBrier − dBrier(prod_curve)):**

| policy | model Brier | dBrier_vs_mkt | dRPS_vs_mkt | dCRPS_vs_mkt | dCenterMAE_vs_mkt | vs_prod |
|---|---:|---:|---:|---:|---:|---:|
| **prod_curve** (current) | 0.1765 | +0.0889 | +0.0818 | +0.331 | +0.52 | +0.0000 |
| w0_nbm (HRRR off) | 0.1943 | +0.1068 | +0.1057 | +0.474 | +0.69 | **+0.0178 (worse)** |
| w0_gfs (NBM+0.30 GFS) | 0.1860 | +0.0985 | +0.0967 | +0.436 | +0.64 | +0.0096 (worse) |
| flat_0.30 (HRRR w=0.30) | 0.1739 | +0.0864 | +0.0822 | +0.364 | +0.55 | −0.0026 |
| cap_0.50 (curve ≤0.50) | 0.1738 | +0.0862 | +0.0814 | +0.341 | +0.52 | −0.0027 |

**dBrier_vs_mkt by local-hour band** (n: ≤12=33, 13–14=96, 15–16=142, ≥17=20):

| band | prod_curve | w0_nbm | w0_gfs | flat_0.30 | cap_0.50 |
|---|---:|---:|---:|---:|---:|
| ≤12 | +0.0761 | +0.0984 | +0.0791 | +0.0773 | **+0.0722** |
| 13–14 | +0.0870 | +0.0855 | +0.0774 | **+0.0713** | +0.0810 |
| 15–16 | +0.0924 | +0.1127 | +0.1055 | +0.0888 | **+0.0858** |
| ≥17 | **+0.0941** | +0.1797 | +0.1802 | +0.1557 | +0.1362 |

**Findings:**

1. **Turning HRRR off is worse overall** — `w0_nbm` degrades every metric vs
   `prod_curve` overall (Brier +0.0178, RPS, CRPS, center all worse) and **materially
   in most bands, especially 15–16h (+0.1127 vs +0.0924) and ≥17h (+0.1797 vs
   +0.0941)**. The one exception is 13–14h, where `w0_nbm` is *marginally* better on
   Brier (+0.0855 vs +0.0870). So the HRRR blend is **net-beneficial** despite HRRR's
   worse standalone point-MAE (§3): a *partial* blend toward a decorrelated center
   reduces distribution error. This **refutes the §3/§5 inference** and the earlier
   "test w=0 as the leading candidate" recommendation.
2. **Replacing HRRR with GFS (`w0_gfs`) is also worse** than the HRRR blend.
3. **The production curve is mildly too aggressive mid-afternoon — on Brier (and, for
   `cap_0.50`, RPS).** `cap_0.50` and `flat_0.30` beat `prod_curve` by only
   **−0.0027 / −0.0026 Brier** overall, concentrated at 13–14h (flat_0.30 best,
   +0.0713 vs +0.0870) and 15–16h (cap_0.50 best, +0.0858 vs +0.0924) — where the
   curve weights HRRR 0.78–0.92. **The improvement is not "better distribution across
   the board":** `flat_0.30` *worsens* RPS (+0.0822 vs +0.0818), CRPS (+0.364 vs
   +0.331) and center MAE (+0.55 vs +0.52); `cap_0.50` is the cleaner candidate
   (RPS +0.0814 ≈ slightly better, center +0.52 ≈ equal) but still **slightly worsens
   CRPS** (+0.341 vs +0.331). So the correct framing is a **small Brier/RPS
   damage-reduction** (`cap_0.50`), not a distributional win.
4. **At ≥17h the high weight is correct** — `prod_curve` is best in that band
   (+0.0941 vs cap +0.1362), consistent with HRRR locking in the late-day high
   (n=20, small).
5. **Marginal, and no edge.** The best policy improves Brier by ~0.003 and leaves the
   market gap at ~**+0.086** — damage-reduction-marginal at most; the market still wins.

**Recommendation (revised):** **Keep the HRRR blend.** Do not disable it (w=0 loses).
The only supported tweak is `cap_0.50` (a modestly lower mid-afternoon weight) as a
**small Brier/RPS damage-reduction candidate** — in-sample, not a validated fix, worth
~0.003 Brier, and it does **not** improve the full distribution (CRPS ~flat-to-worse).
`flat_0.30` is rejected (improves Brier only, worsens RPS/CRPS/center). Given the size,
this is **low priority**. If pursued: implement behind a research flag (default =
current curve), re-score through the production-like path (calibrator included), and
validate walk-forward without tuning the weight in-sample. A *fitted* by-hour curve
remains deferred (high overfit risk); `cap_0.50`/`flat_0.30` bracket it.

**Statistical-rigor note (added after the EXP-B3 review):** the "w=0 loses" deltas here
are *point estimates*; a paired per-event CI was not computed for EXP-B2 (it was for
EXP-B3/GFS). The HRRR effect is large — `w0_nbm − prod_curve` is +0.0179 Brier overall
(and +0.0203 at 15–16h, +0.0856 at ≥17h), i.e. **~12× the GFS effect (+0.0015)** whose
paired CI just barely included 0 at n≈263 — so the HRRR conclusion is very likely
statistically robust, but a paired CI/bootstrap should be added if this is ever
promotion-relevant. The mid-afternoon `cap_0.50` improvement (~0.003 Brier) is small
enough that it, like GFS, should NOT be treated as established without a paired CI.

**Overfitting risk:** Low for the "keep the blend / w=0 loses" conclusion (large effect,
robust overall and in the high-n 15–16h band). Medium–High for any fitted curve.
