# Experiment Plan — Next

**Date:** 2026-06-06
**Status:** Pre-registration (per `WEATHERBOT_EXPERIMENT_REGISTRY.md` discipline)
**Constraint:** All experiments are research-only behind flags or in non-production
harnesses. Nothing here may change live probabilities, sizing, or trade entry. No
new trading strategies, no Kelly/TP/SL tuning, no gates, no ensemble promotion, no
new production weather models. Promotion requires `WEATHERBOT_PROMOTION_CRITERIA.md`.

The market baseline is **confirmed**, and the audit + independent analyst review
(2026-06-06) support the stronger statement: WeatherBot's current forecast
distribution is **materially worse** than the market-implied distribution, even
after correcting the benchmark's biggest methodological flaw. The plan therefore
prioritizes (a) hardening the benchmark, (b) validating mechanical fixes as
damage-reduction, and (c) the one decisive open question —
**can any forecast center beat the market out of sample?**

---

## Canonical action order (agreed — auditor + analyst review, 2026-06-06)

This is the single authoritative sequence. Lock the ruler before changing anything
it measures; fix wrong-direction model mechanics before hygiene.

0. ✅ **Lock the benchmark** — coherent-snapshot is now canonical + frozen
   regression test added (9 tests). *(EXP-A1, A2 — DONE 2026-06-06)*
1. ◑ **METAR vs CLI floor** — EXP-B1 in-sample experiment done: soft floor is the
   in-sample candidate (winner<5% starvation 7→1, all metrics improved vs hard floor)
   but damage-reduction only; **not a validated fix** — production-like re-score +
   walk-forward OOS validation still required before any production change. *(EXP-B1)*
2. ◑ **HRRR weight curve** — EXP-B2 in-sample done: HRRR blend is **net-helpful**
   (w=0 loses); curve only mildly too aggressive 13–16h (~0.003-Brier tweak). **Keep
   the blend**; lower-mid-afternoon-weight is a low-priority candidate, not validated.
   Corrected the earlier point-MAE-based "defect" overstatement. *(EXP-B2)*
3. ◑ **GFS blend** — EXP-B3 in-sample done: false premise corrected in code; paired CI
   shows the 0.30 blend is **statistically indistinguishable** from NBM-only at lead-1
   (full GFS significantly worse). **Keep 0.30**; no weight change warranted. *(EXP-B3)*
4. **Calibrator** — rebuild from all-forecasts-vs-CLI and restore ladder
   normalization. ← **NEXT** *(EXP-B4)*
5. **Reliability metric** — replace the dormant invalid metric. *(EXP-B5)*

Running continuously alongside: **EXP-C1** (can any center beat the market OOS),
evaluated against the stopping rule. EXP-C2 only if C1 shows a positive-skill center.

> Step 3's code-comment correction is the one production-file touch that does not
> change behavior; everything else is research-only behind flags or in harnesses.

---

## P0 — Harden the benchmark (do first; everything else depends on it) — ✅ DONE 2026-06-06

### EXP-A1 — Adopt coherent-snapshot selection — ✅ DONE
- **Hypothesis:** The production benchmark's latest-per-bucket selection is
  time-incoherent (median 9.2 h spread at lead 0) and overstates CRPS/center by ~40%.
- **Action taken:** `coherent_snapshot` is now the **default/canonical** selection
  inside `research/market_relative_center_benchmark.py` (`collect_coherent_snapshot_rows`);
  the legacy `latest_per_bucket` is kept behind `--selection latest_per_bucket` for
  audit. `snapshot_market_benchmark.py` now delegates to the canonical collector
  (one implementation). Report header states the selection + snapshot diagnostics.
- **Result:** canonical run reproduces the audit's coherent numbers exactly —
  weighted Brier **+0.0684**, RPS **+0.0688**, CRPS **+0.286 F**, 561/561 events,
  34/34 market-better groups, median intra-snapshot spread 0.01 h. Legacy flag still
  reproduces the original report (+0.0645/+0.0668/+0.454).
- **Overfitting risk:** Low. **Leakage controls:** none needed (descriptive).

### EXP-A2 — Frozen regression fixture — ✅ DONE
- **Action taken:** `tests/test_market_relative_center_benchmark.py` extended with
  hand-computed frozen scoring values (Brier 0.08/0.02, RPS 0.04/0.01, CRPS 0.16/0.04,
  center 71.0) plus selection tests (latest-window pick, min-buckets, ticker dedup,
  drop-when-insufficient) and a normalization-repair test. **9 tests pass**, DB-free.
- **Pass:** CI/local now catches scoring or selection regressions.

---

## P0 — Mechanical fix validations (damage reduction, not edge)

Each is gated on the **coherent-snapshot market benchmark**, lead-0 unless noted,
out of sample (walk-forward where a parameter is fit). Pass = improves or neutralizes
market-relative Brier/RPS with no leakage; fail = degrades or only helps in-sample.

### EXP-B1 — CLI-consistent floor basis  *(DEFECT_METAR_CLI_FLOOR.md §9)* — ◑ IN-SAMPLE EXPERIMENT DONE 2026-06-06
- **Harness:** `research/floor_basis_experiment.py` (research-only; PIT rebuild,
  floor varied, scored on canonical benchmark). 291 lead-0 event groups; 289–290
  scored per policy. **Pre-calibrator** raw CDF (isolates the floor).
- **Result (in-sample):** the **soft floor** (cap injected confidence; `soft_w0.50`)
  is the in-sample diagnostic **winner** — improves on the production hard floor
  across Brier/RPS/CRPS/center MAE and cuts `winner<5%` mass starvation **7→1**.
  δ-subtraction (fixed + walk-forward p85≈0.6F) and floor-off are rejected (fat-tail
  over-reads / Brier loss). **Damage reduction only: market gap stays +0.0837 Brier.**
- **Status: candidate, NOT a validated fix. No production change made.** Required
  before any production-facing decision: (1) implement soft floor behind a research
  flag (default = current hard floor); (2) **re-score through the full production-like
  path (calibrator included)**, not the pre-calibrator raw CDF used here; (3) validate
  **walk-forward on fresh lead-0 station-days** without tuning the weight; (4) confirm
  no leakage.
- **Overfitting risk:** Low–Medium (one fixed weight; do not optimize on this window).

### EXP-B2 — HRRR weight curve  *(DEFECT_HRRR_WEIGHT_CURVE.md §9)* — ◑ IN-SAMPLE EXPERIMENT DONE 2026-06-06
- **Harness:** `research/hrrr_weight_experiment.py` (research-only; NBM-only center
  rebuilt PIT, HRRR/GFS shift re-applied per policy, scored by hour band). Pre-calibrator.
- **Result (in-sample, supersedes the §3/§5 point-MAE inference):** the HRRR blend is
  **net-helpful** — `w=0` (NBM-only) is *worse* (dBrier +0.0889→+0.1068). The
  production curve is only **mildly too aggressive at 13–16h**: `cap≤0.50` / `flat-0.30`
  beat prod by only **−0.003 Brier**; at ≥17h the high weight is correct. **Damage
  reduction marginal; market gap stays ~+0.086.**
- **Status: keep the blend (do NOT disable).** The lower-mid-afternoon-weight tweak is
  a low-priority in-sample candidate, **not a validated fix**. Before any production
  change: research flag (default = current curve) → production-like re-score → walk-forward
  OOS, no in-sample weight tuning. A *fitted* by-hour curve is deferred (high overfit risk).
- **Overfitting risk:** Low for "keep blend / w=0 loses"; High for any fitted curve.

### EXP-B3 — GFS blend re-derivation  *(DEFECT_GFS_BLEND.md §9)* — ◑ IN-SAMPLE EXPERIMENT DONE 2026-06-06
- **Done:** false MAE claim corrected in `models/distribution.py` (comment-only).
- **Harness:** `research/gfs_blend_experiment.py` (research-only; NBM-only center rebuilt
  PIT, GFS weight sweep, scored by lead). Pre-calibrator.
- **Result (in-sample, lead-1 primary; paired CI added per reviewer):** the 0.30 blend is
  **statistically indistinguishable** from NBM-only — paired `gfs_off − prod_0.30` ΔBrier
  +0.0015, CI **[−0.0013, +0.0043] includes 0**. The point estimate slightly favors the
  blend but is within noise. The **one established result: full GFS (w=1) is significantly
  worse** (paired ΔBrier +0.0170, CI [+0.0106, +0.0234] excludes 0). 0.15 ≈ 0.30.
- **Status: keep 0.30, no weight change** — defensible because the blend is *not harmful*
  (≈ NBM-only) and high weights are worse; removing it has no established benefit either.
  Marginal; lead-1 market gap stays +0.0235. Not a production change. ECMWF deferred.
- **Overfitting risk:** Low for "full GFS loses / 0.30 not harmful"; claiming the blend
  *improves* on NBM-only would overfit a within-noise point estimate.

### EXP-B4 — Calibrator rebuild  *(CALIBRATOR_REBUILD_REPORT.md)*
- **Action:** Build all-forecasts-vs-CLI reliability (rebuild distributions for all
  settled station-days, raw `prob_between`, bin by **raw**, walk-forward frozen).
  Separately, renormalize calibrated bucket probs across the ladder.
- **Metrics:** Brier, Log Loss, RPS, CRPS, market-relative skill.
- **Pass:** OOS improvement, no market-relative degradation (§3 criteria).
- **Overfitting risk:** High → coarse pooling, shrinkage, walk-forward.

### EXP-B5 — Reliability metric replacement  *(RELIABILITY_METRIC_REPORT.md)*
- **Action:** Replace `verification/metrics.py` reliability with true
  predicted-vs-observed per-bucket curve + unit test; relabel dashboard "raw" chart.
- **Overfitting risk:** None (correctness).

---

## P1 — The decisive question: can any center beat the market?

### EXP-C1 — Market-relative forecast-center benchmark *(EXP-2026-007; EXP_C1_FORECAST_CENTER_BENCHMARK.md)*
- **Hypothesis (to falsify):** No available center beats the market-implied center OOS.
- **◑ First pass DONE 2026-06-06 (parameter-free centers):** `research/center_market_benchmark.py`
  scored NBM / GFS / ECMWF / HRRR / NBM-GFS-ECMWF decorrelation blend market-relative by
  lead, with paired CIs. **Result: NO center beats the market** — all positive
  market-relative Brier+RPS at both leads. NBM-only is the best center at lead-1; nothing
  beats it there; HRRR best at lead-0 (beats NBM, still loses to market). Decorrelation
  blend ≈ NBM. (In-sample, pre-calibrator; deterministic centers raw — see report §7.)
- **✅ EXP-C1b DONE 2026-06-07 (walk-forward, pre-registered, run on VPS) — the remaining
  hope is exhausted:** `research/center_market_benchmark_wf.py` scored the 6 locked
  conditioned centers (bias-corrected GFS/ECMWF/HRRR, inverse-MAE blend, |NBM−GFS| regime
  gate, lead-0 obs-anchor), walk-forward, Bonferroni-6. **Result: NO variant passes** — all
  still lose to the market; none significantly beats NBM-only. (A lead-1 reconstruction-
  alignment bug was found+fixed on the VPS; lead-0 unaffected.) See
  `EXP_C1B_FORECAST_CENTER_WF.md` / `EXP_C1B_PREREGISTRATION.md` / EXP-2026-008.
- **Pass (program-relevant):** any center reaches **negative** market-relative Brier AND
  RPS (CI excluding 0), OOS, ≥2 stations, ≥2 regimes. **→ Not met by any center (C1 or C1b).**
- **DECISION (pre-committed, C1b prereg §6): recommend the observation-only pivot** (charter
  §7); calendar backstop 2026-09-04 / 500 fresh station-days; operator's final call.
- **Leakage controls (applied):** `valid_date < station_local_date(as_of)` cutoff;
  lead-aligned trailing reconstruction; truth = settlement only.

### EXP-C2 — Disagreement right-vs-wrong research  *(blocked-from-deploy)*
- **Hypothesis:** A pre-registered, signal-time-observable subset of model/market
  disagreements is one where WeatherBot is more often closer to truth.
- **Action:** Build the labeled dataset (model vs market closer to CLI), evaluate
  pre-registered features (forecast recency, HRRR/NBM agreement, move-without-move,
  lead, regime). **Do not deploy.**
- **Pass:** subset beats market OOS with CIs excluding 0.
- **Overfitting risk:** Very High → pre-register features before looking; OOS only.

---

## Pre-registered stopping rule (from the charter/criteria)

Continue as trading research **only while** EXP-C1 is open and unfalsified. If, after
**500 fresh station-days or 90 calendar days** (whichever first, i.e. ~2026-09-04), no
center variant has cleared positive market-relative RPS **and** Brier out of sample on
the required sample, **stop trading research and convert WeatherBot to
observation-only analytics** (charter §7, criteria §8). Mechanical-fix experiments
(EXP-B*) may complete regardless, as hygiene.

## Sequencing

Follows the canonical action order above:

1. **EXP-A1, A2** — lock the benchmark (coherent-snapshot canonical + fixture) — days.
2. **EXP-B1 → B2 → B3** — wrong-direction model mechanics (floor → HRRR → GFS),
   each research-only and validated on the locked benchmark — ~1–2 weeks.
3. **EXP-B4, B5** — hygiene (calibrator rebuild + normalization, reliability metric).
4. **EXP-C1** — the decisive test; runs continuously, accumulating fresh
   station-days, evaluated against the stopping rule.
5. **EXP-C2** — only if C1 shows any positive-skill center.

## What would justify changing trading logic (none met today)

A change may touch production probabilities/sizing/entry **only** after a forecast
variant clears EXP-C1's pass bar (positive market-relative RPS **and** Brier, OOS,
≥100–250 fresh station-days, ≥2 stations/regimes, no leakage) **and** the mechanical
prerequisites are fixed. Until then, paper mode remains mandatory.
