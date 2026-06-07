# Defect Report: HRRR Late-Day Weight Curve

**Date:** 2026-06-06
**Defect class:** Overfit blend weight on the less-accurate center model
**Severity:** Medium–High (same-day TMAX, peak-heating hours)
**Recommendation:** Demote the HRRR center blend to research-only and re-derive the
weight curve from a by-hour HRRR-vs-NBM accuracy study; do not keep the current
curve in production.

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

1. Build a research-only by-hour, by-lead HRRR-vs-NBM center study (issuance-time
   correct), and a candidate weight curve (including **w=0** as the null).
2. Score candidates on `snapshot_market_benchmark.py` (Brier/RPS/CRPS/center MAE
   vs market), lead-0, out of sample, by hour band.
3. Until then, treat the production curve as **unsupported**: a research flag to set
   HRRR weight to 0 (NBM/GFS-decorrelation center only) should be tested as the
   leading damage-reduction candidate. Promote nothing without OOS market-relative
   evidence (`WEATHERBOT_PROMOTION_CRITERIA.md` §2/§4).
