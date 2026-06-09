# EXP-2026-013 — Shadow-Ensemble Market-Relative Benchmark (Pre-Registration)

**Date:** 2026-06-09
**Status:** LOCKED 2026-06-09 (written before any ensemble row was scored against the market).
**Type:** Forecast-information gate check. Research-only, paper-only, no production change.

> Locked fields: models (§3), variants (§4), as-of rule (§5), cohort (§6), pass bar (§7).
> No post-hoc variant additions, no smoothing/weight search, no slice mining. All results
> reported including nulls.

---

## 1. Why this exists (and why it does not violate the closed accuracy axis)

The accuracy closure (EXP-C1/C1b/C2) was explicitly conditioned on "current public data and
models," and named **genuinely new models** as the reopening trigger. The 2026-06-09 program
review found that `ensemble_forecast` has been collecting **four full ensembles in shadow
since 2026-05-10/15 that were never scored market-relative**: WEATHERNEXT2 (64 members),
ECMWF_IFS_ENS (51), ECMWF_AIFS_ENS (51), GFS_ENS (31). Two of these (WeatherNext 2, AIFS)
are AI models that did not exist in the C1/C1b variant set. C1/C1b tested *deterministic*
GFS/ECMWF/HRRR centers and NBM percentiles only.

This is therefore not a re-mine of tested variants; it is the named reopening trigger,
executed once, with the same harness and bar that closed the axis.

## 2. The single question

Does any shadow ensemble — as a center or as a full member-frequency distribution — beat the
Kalshi market-implied distribution on the canonical coherent-snapshot benchmark?

**Honest prior: negative.** Professional participants likely consume the same ensembles. The
value is closing the named gap with a measurement instead of an assumption.

## 3. Models (LOCKED)

`WEATHERNEXT2`, `ECMWF_IFS_ENS`, `ECMWF_AIFS_ENS`, `GFS_ENS` from `ensemble_forecast`
(var `TMP_2M`, values already °F). No other models.

## 4. Variants (LOCKED — three per model, twelve total)

Per-member daily TMAX = max of the member's values whose `valid_time` falls in the
station-local `valid_date` (stations.tz). Then:

1. **`<m>_center`** — ensemble median of member daily TMAX, swapped into the NBM-only shape
   exactly as EXP-C1 (`cdf.shift = base + (center − nbm_median)`). Raw, un-bias-corrected.
2. **`<m>_center_bc`** — same, with a walk-forward bias correction: subtract the trailing
   mean residual (`<m>_center` − CLI tmax) over strictly-prior valid_dates for the same
   station/model/lead (min 5 prior days, max 30, no same-day or future data). This is the
   C1b bias-correction method and the fair read for WEATHERNEXT2, whose 6-hourly
   instantaneous sampling (28 valid_times/run vs 180 hourly for the others) is expected to
   bias raw daily-max cold by a roughly constant margin.
3. **`<m>_dist`** — the ensemble itself as the distribution: bucket probability =
   (member count in bucket + 0.5) / (members + 0.5·buckets) over the event's captured bucket
   set (fixed Laplace-0.5 smoothing, no search), normalized. Tests member *spread*
   information, which the C1 center-swap method cannot.

Baseline: `nbm_only` (bias-corrected NBM, as C1). Comparator: the market (paired per event).

## 5. As-of rule (LOCKED — the leakage guard)

Run selection uses **`ingested_at <= snapshot_ts`** (the row was physically in our database
at scoring time), choosing the max `run_time` among qualifying runs. `run_time` is treated as
untrusted metadata: the Open-Meteo-sourced ensembles show implausibly small
(`ingested_at − run_time`) medians (~0.7 h), so selecting on stamped run_time could leak a
run we did not actually have. `ingested_at` selection is leakage-safe regardless of labeling.

## 6. Cohort (LOCKED)

Canonical coherent-snapshot events (`collect_coherent_snapshot_rows`, tick 10 min,
min 3 buckets), var `TMAX_DAILY`, leads 0–1, all events where at least one locked model has
a qualifying run. Sample window is bounded by ensemble collection start (2026-05-10) —
roughly 20 stations × ~3.5 weeks × 2 leads. These station-days were never examined for these
models (parameter-free first pass, same epistemic status as EXP-C1 first pass); `_center_bc`
is walk-forward within the window.

## 7. Pass bar and decision rule (LOCKED — charter-identical)

A variant is a **candidate** only if: market-relative **Brier AND RPS are negative** (beats
market) with the paired per-event 95% CI excluding 0 (events are unique station-date-lead, so
event-level pairing is cluster-correct), n ≥ 100 events, market-beating sign in ≥ 2 stations.

- **Candidate found:** open a fresh-forward-data pre-registration (promotion still requires
  `WEATHERBOT_PROMOTION_CRITERIA.md` on fresh station-days; this run alone promotes nothing).
- **No candidate:** the genuinely-new-models trigger is consumed and the accuracy axis stays
  closed with the gap documented as tested. No re-runs of these models without a new prereg.

In both branches: no production probability, sizing, gating, or execution change.

## 8. Limitations (stated up front)

- NBM baseline is bias-corrected; ensemble centers raw (mitigated by `_center_bc`), matching
  the C1 limitation.
- WEATHERNEXT2 6-hourly sampling under-measures daily max; `_dist` and `_center` both carry
  it; `_center_bc` is the fair variant.
- Window is one summer month; any candidate needs fresh-season forward data by design.
- `_dist` spread is the model's raw spread; no calibration layer is fitted (deliberate:
  parameter-free).

## 9. Artifacts

`research/ensemble_market_benchmark.py` (harness, research-only), results to
`EXP_2026_013_RESULTS.md` + registry EXP-2026-013. Evidence run on the VPS.
