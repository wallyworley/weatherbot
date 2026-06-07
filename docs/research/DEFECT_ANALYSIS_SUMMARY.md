# Defect Analysis Summary

**Date:** 2026-06-06
**Scope:** Five mechanical defects audited against the 2026-06-07 market-relative
benchmark. All work is research-only; no production trading behavior was changed.

See per-defect reports: `DEFECT_METAR_CLI_FLOOR.md`, `DEFECT_HRRR_WEIGHT_CURVE.md`,
`DEFECT_GFS_BLEND.md`, `CALIBRATOR_REBUILD_REPORT.md`, `RELIABILITY_METRIC_REPORT.md`.
Benchmark validity: `MARKET_BASELINE_AUDIT.md`.

---

## 1. Headline

The benchmark is valid and reproduces: **WeatherBot does not beat the market-implied
forecast** on Brier, RPS, CRPS or center MAE at either populated lead day. Three of
the five mechanical defects are confirmed and contribute to (but do **not** fully
explain) the lead-0 deficit; two are correctness defects with little effect on the
market gap. **None of the fixes, individually or together, is likely to close the
gap** — the binding constraint is forecast information / center resolution.

## 2. Defect scorecard

| Defect | Confirmed? | Direction | Quantified impact | Affects market gap? | Fix priority |
|---|---|---|---|---|---|
| **METAR vs CLI floor** | ✅ Yes | Wrong-direction (manufactures false divergence) | Floor > CLI truth on **20%** of lead-0 events; literal winner truncation on **2.1%**. EXP-B1: soft floor (in-sample) cuts `winner<5%` 7→1 and beats hard floor on all metrics — **damage reduction only**, market gap stays +0.0837 | Yes (lead-0 contributor) | **1** |
| **HRRR weight curve** | ◑ Revised (EXP-B2) | Blend net-**helpful**; curve only mildly too aggressive 13–16h | **EXP-B2 refuted the "inferior model" read:** HRRR-off is *worse* overall (dBrier +0.0889→+0.1068; materially at 15–16h & ≥17h, ~flat at 13–14h). `cap_0.50` = small **Brier/RPS** damage-reduction (~−0.003 Brier), CRPS ~flat-to-worse; `flat_0.30` worsens RPS/CRPS/center. ≥17h high weight correct. (§3 point-MAE table true but incomplete) | Marginal | low (revised down) |
| **GFS center blend** | ✅ Yes (false premise) | Justification false; small decorrelation help | PIT MAE: NBM 1.571/1.654 **beats** GFS 1.815/2.176 (lead 0/1); blend gives only ~3–8% center-MAE, no market skill | Marginal | **3** |
| **Signal-log calibrator** | ✅ Yes (structural) | Circular; near-inert | Fires on 98.7% of signals, **net −0.4 pp**; 24.8% train/infer bin mismatch; breaks ladder normalization (prob-sum median 1.13, max 3.25) | No (net ~0) | 4 (correctness) |
| **Invalid reliability metric** | ✅ Yes | N/A (measurement) | `empirical_freq` is a histogram density over a degenerate event; **dormant** (table stale since 2026-04-20) | No | 5 (correctness) |

## 3. What the defects do and don't explain  *(updated after EXP-B1/B2)*

- **The floor is the one confirmed wrong-direction defect.** It manufactures
  over-confident same-day distributions that diverge from the market in the losing
  direction, concentrated at **lead 0** (coherent-snapshot lead-0 Brier +0.103 vs
  lead-1 +0.032). EXP-B1: a soft floor removes most of that self-inflicted damage
  in-sample (`winner<5%` 7→1) but does **not** close the market gap.
- **HRRR was NOT a wrong-direction defect** (EXP-B2 correction). The afternoon HRRR
  blend *improves* market-relative scores vs NBM-only; the production curve is only
  mildly too aggressive at 13–16h (a ~0.003-Brier tweak). The earlier center-MAE read
  overstated this. (The 06–09 morning ablation separately shows morning HRRR ≈ inert.)
- **They do not explain the whole gap.** The coherent-snapshot benchmark shows
  WeatherBot losing on **every** metric at **both** leads, and the morning ablation
  shows **NBM-only** still at market skill **−0.30**. Removing/limiting the defects
  reduces self-inflicted damage; it does not create forecast information the model lacks.
- **The calibrator and reliability metric are correctness problems, not edge
  problems.** The raw model is already roughly calibrated; the calibrator nets ≈0;
  the reliability metric is dormant. Fixing them is hygiene, not a path to edge.

## 4. Cross-cutting finding: the production stack is no better than clean NBM

The morning ablation (45d, n=1313, **06–09 window**) shows `logged_model` (full
production stack: bias + HRRR/GFS blend + calibrator) is **worse** than a clean
`nbm_only` rebuild on RPS (0.1383 vs 0.1188) and equal on Brier. In the morning the
machinery is net-neutral-to-negative versus simply using NBM. (This is window-specific:
EXP-B2 shows the *afternoon* HRRR blend is net-positive — morning HRRR weight is low,
so the morning ablation mostly reflects the calibrator + GFS, not the afternoon HRRR
blend.) Either way the conclusion holds: the mechanical machinery matters for **damage
reduction**, but the **center itself** (even clean NBM) is what loses to the market.

## 5. Recommended fix order (agreed — auditor + analyst review, 2026-06-06)

Lock the ruler before changing anything it measures; fix wrong-direction model
mechanics before hygiene. (Canonical version lives in `EXPERIMENT_PLAN_NEXT.md`.)

0. **Lock the benchmark** — coherent-snapshot canonical + frozen regression fixture. *(done)*
1. **METAR/CLI floor** — highest wrong-direction, lead-0 impact. *(EXP-B1 done: soft-floor candidate, in-sample)*
2. **HRRR weight curve** — *(EXP-B2 done: w=0 REJECTED — keep the blend; optional lower
   mid-afternoon weight (cap≈0.50) is a small Brier/RPS damage-reduction candidate,
   research-only / OOS)*.
3. **GFS blend** — false claim corrected in code; re-test GFS as decorrelation-only. *(next)*
4. **Calibrator** — rebuild from all-forecasts-vs-CLI, walk-forward, binned by raw; restore ladder normalization.
5. **Reliability metric** — replace with true predicted-vs-observed curve + test.

Each must be validated on `snapshot_market_benchmark.py` out of sample and may only
enter production under `WEATHERBOT_PROMOTION_CRITERIA.md`. **Expect damage reduction,
not edge.** The forecast-information question (can any center beat the market) remains
open and is the real program risk — see `EXPERIMENT_PLAN_NEXT.md`.

## 6. Statistical limitations (all defects)

- Samples are ~5–6 weeks, ~21 stations, leads 0–1 only; per-station and per-hour
  bins are small.
- Deterministic daily TMAX is `MAX(hourly)`, which inflates GFS/HRRR error somewhat.
- All "impact" figures describe past logged behavior; they are diagnostic, not OOS
  promotion evidence.

## 7. Overfitting risk (all fixes)

Floor: Medium. HRRR curve: High. GFS weight: Medium. Calibrator: High. Reliability
metric: None (correctness). Every fix that introduces a fitted parameter must be
walk-forward frozen and market-relative validated.
