# EXP-C1b — Pre-Registration (walk-forward conditioned forecast centers)

**Date:** 2026-06-06
**Status:** **LOCKED — approved with amendments (2026-06-06); building to spec.**
**Experiment:** EXP-2026-008 (follow-on to EXP-2026-007 / EXP-C1).

**Amendments applied after review (locked):** (1) leakage-safe training cutoff
`valid_date < station_local_date(as_of)` for lead-1; (2) Bonferroni-6 is the single
required multiple-comparison rule (chronological held-out = confirmation only, not a pass
path); (3) `obs_anchor_l0` hour buckets fixed to {<10,10–11,12–13,14–15,≥16}, fallback
remaining-rise=0 if <8 samples; (4) inverse-MAE weight = 1/(MAE+0.5), report drop rates;
(5) pass judged per applicable lead cohort (not pooled), single-lead variants use two
chronological time-halves for robustness. No new variants added.
**Purpose:** the charter's final, defensible test of Q3 — *can any forecast center beat
the market-implied center out of sample?* — removing the EXP-C1 first-pass limitations
(raw deterministic centers, fixed weights, no conditioning) while controlling overfitting.

> This document is **locked once approved.** No variant may be added, removed, or
> re-specified after sign-off; all pre-registered variants are reported including failures.
> Changes require a new pre-registration.

---

## 1. Hypothesis (to falsify)

No conditioned/bias-corrected/dynamically-weighted forecast center — built only from data
available at the forecast timestamp — beats the market-implied center out of sample on
market-relative Brier **and** RPS.

The prior is **unfavorable** (EXP-C1 first pass + B1–B3 all negative). C1b exists to make a
kill/continue decision defensible, not because we expect a winner.

## 2. Fixed variant list (EXACTLY these — no additions post-approval)

Baselines (reference, not candidates): `market` (target), `nbm_only` (current best center;
a candidate must beat **both** market and nbm_only to count).

Candidate centers (all walk-forward; all keep NBM spread/shape, swap only the center, as in
EXP-C1):

| # | name | definition (point-in-time, prior-day-trained only) |
|---|---|---|
| 1 | `gfs_bc` | GFS daily-TMAX − trailing **station×lead** bias (mean GFS−CLI over prior 30 settled days, min 8 samples; else 0) |
| 2 | `ecmwf_bc` | ECMWF daily-TMAX − trailing station×lead bias (same rule) |
| 3 | `hrrr_bc` | HRRR daily-TMAX − trailing station bias (lead-0 only; same rule) |
| 4 | `invmae_blend_bc` | weighted mean of {NBM, `gfs_bc`, `ecmwf_bc`} with **weight = 1/(trailing-30d MAE vs CLI + 0.5)** (the +0.5 floors tiny MAEs from dominating), renormalized over available models. Report per-model **drop/fallback rates** (esp. ECMWF). |
| 5 | `regime_agree` | **one** pre-specified regime variable = \|NBM−GFS\| at as_of. If ≤ τ (τ = trailing-30d median \|NBM−GFS\| for that station×lead) → `nbm_only`; else → `invmae_blend_bc` |
| 6 | `obs_anchor_l0` | lead-0 only: center = NBM_median shifted toward (metar_max_so_far + trailing expected remaining rise). Expected-remaining-rise = trailing-30d mean of (CLI − metar_max-at-this-local-hour) by **station × fixed local-hour bucket ∈ {<10, 10–11, 12–13, 14–15, ≥16}**. **Fallback: if <8 trailing samples in the bucket, remaining-rise = 0** (center = NBM_median). |

Six candidates. No per-station hand-tuning, no free thresholds beyond the trailing
statistics above. Trailing window fixed at **30 days**, min-samples **8** (pre-set; not swept).

## 3. Walk-forward protocol (leakage controls)

- **Training cutoff (amended per review — leakage-safe for lead-1):** for each scored
  event with timestamp `as_of`, every trailing statistic (bias, MAE weights, regime
  threshold τ, remaining-rise climatology) uses **only settled events whose truth would
  have been known before `as_of`** — operationally, **valid_date < `station_local_date(as_of)`**
  within the 30-day window. (`valid_date < d` alone is NOT enough: for a lead-1 snapshot
  taken the evening before the target, the prior day's CLI is not yet published, so the
  prior day must be excluded. The station-local-date rule excludes it conservatively. If a
  reliable settlement-publish timestamp is available it may be used instead, but the
  conservative cutoff is the locked default.)
- Each trailing day's forecast value is reconstructed **point-in-time at the same
  station-local time of day as `as_of`** (mirrors the existing `_deb_recent_mae_center`
  approach in `morning_center_ablation.py`), so trailing errors use only what was knowable
  then.
- Forecast values use `run_time ≤ as_of` (= coherent-snapshot ts), as in EXP-C1.
- Truth = settlement (`expiration_value`/CLI) only.
- Scored on the **canonical coherent-snapshot benchmark** events (lead 0–1), same market
  midpoints, same `score_event` scoring as every prior experiment.
- Pre-calibrator (isolates the center, consistent with C1; production-like calibrator
  re-score is a separate pre-promotion step if anything passes).

## 4. Metrics

Per variant, by lead and overall: market-relative Brier, RPS, CRPS, center MAE; **paired
per-event CI** vs `market` and vs `nbm_only`. Report n everywhere. Report ALL six variants.

## 5. Pass criteria (tight; anti-slice-mining)

**Pass is judged within the variant's applicable lead cohort, NOT on pooled lead-0+lead-1
averages.** `hrrr_bc` and `obs_anchor_l0` are lead-0 only → judged on lead-0. Variants
applicable to both leads are judged per lead and may pass in either cohort (report both).

A variant **passes** in a cohort only if ALL hold:

1. **Beats the market:** negative market-relative Brier **and** RPS in that cohort, each
   with a paired CI excluding 0.
2. **Beats nbm_only:** negative paired ΔBrier **and** ΔRPS vs `nbm_only`, CI excluding 0.
3. **Multiple-comparison guard (amended — single primary rule):** because 6 variants are
   tested, criteria 1–2 must hold at a **Bonferroni-adjusted level α = 0.05/6 (≈99.2% CIs)**.
   This is the **required** rule. A **chronological held-out split** (train trailing stats
   on the earlier half, evaluate on the later half) is **reported as a confirmation check
   only** — it is *not* an alternate pass path.
4. **Robustness:** the negative (better-than-market) point estimate holds in **≥2 stations**
   **and** **≥2 splits** — for both-lead variants the two leads count; for single-lead
   variants use **two chronological time-halves**. Not a single slice.
5. **Minimum sample:** ≥100 scored station-days in the cohort on which a pass is claimed,
   **counting events where the variant actually applied its correction** (not fallback).

A variant that ties market or nbm_only (CI includes 0) is **not** a pass.

## 6. Decision rule (pre-committed)

- **If ≥1 variant passes §5:** it becomes a forecast **candidate** (not promoted). It then
  requires the full `WEATHERBOT_PROMOTION_CRITERIA.md` path: production-like re-score
  (calibrator included) and validation on **fresh** OOS station-days before any production
  change. Report it; do not touch production.
- **If NO variant passes:** this — combined with the benchmark audit, B1–B3, and the C1
  first pass — is treated as **sufficient analytical evidence that WeatherBot has no
  available forecast-information edge.** Recommend converting to **observation-only
  analytics** (charter §7). Note: the *formal* kill threshold remains 500 fresh
  station-days or 2026-09-04; C1b being walk-forward on the existing ~6-week window is a
  strong OOS estimate, so the recommendation is to pivot rather than wait, but the user
  makes the final calendar call.
  - **Coverage caveat (transparency):** a variant whose **correction was applied on <100
    station-days** (high fallback/drop rate — likely `ecmwf_bc` and the ECMWF leg of the
    blend early in the window) is labeled **"inconclusive — data-limited," not "rejected."**
    The strong-evidence claim rests on the well-covered variants (NBM-shape, GFS/HRRR
    bias-corrected, regime, obs-anchor where samples suffice) failing. Per-variant coverage
    is reported so a null is not over-read where data was thin.

## 7. Overfitting risk & mitigations

**Medium–High.** Mitigations baked in above: fixed 6-variant list locked at sign-off;
report all incl. failures; coarse fixed trailing window (30d) and min-samples (8); a single
pre-specified regime variable (|NBM−GFS|), no regime search; **Bonferroni-6 required**
(chronological held-out is a confirmation check only, not an alternate pass path — see §5.3);
robustness across stations+regimes required; no per-station free parameters.

## 8. Known limitations (stated up front)

- Existing ~6-week window; ECMWF from 2026-05-10, so ecmwf_bc trailing windows are short
  early on. Combined with the conservative training cutoff (§3), early events fall back
  (bias 0 / model dropped from the blend). **Per-variant correction-applied coverage is
  reported; thin-coverage variants are labeled "inconclusive — data-limited," not
  "rejected" (§6).**
- Walk-forward on existing data is an OOS *estimate*, not the charter's fresh-station-day
  kill threshold.
- Centers reuse NBM's spread (isolates the center question), consistent with C1.

## 9. What I will build after approval

A single research-only harness `research/center_market_benchmark_wf.py` (or extend
`center_market_benchmark.py` behind a `--walk-forward` flag) implementing exactly §2–§4,
plus a chronological-held-out mode for §5.3. No production code touched. Results →
`EXP_C1B_FORECAST_CENTER_WF.md` + registry EXP-2026-008 Result/Decision.

---

**Requested sign-off:** approve the variant list (§2), the pass criteria (§5), and the
decision rule (§6) — or amend — before I write the harness.
