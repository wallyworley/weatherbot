# WeatherBot — Current State

**Updated:** 2026-06-07
**Trading status:** **Paper only. No live promotion.**
**Program status:** **DECISION (operator-approved 2026-06-07): pivot to observation-only
analytics.** All edge tests negative (audit → B1–B3 → C1 → C1b); no market-relative forecast
advantage found. Trading research is closed unless fresh future evidence reopens the gate.
*(Decision is a program/governance status change; no production trading logic was changed —
the bot was already paper-only.)*

---

## Where we are

The 2026-06-07 market-relative benchmark was audited and **confirmed**:

> **WeatherBot's forecast distribution is materially worse than the Kalshi
> market-implied distribution** on Brier, RPS, CRPS and center MAE — even after
> correcting the benchmark's biggest methodological flaw.

**The full edge investigation is now complete and uniformly negative:**

- **Audit:** benchmark confirmed (coherent-snapshot canonical).
- **B1–B3:** mechanical center fixes (METAR floor, HRRR/GFS blends) are damage-reduction at
  most; none creates edge.
- **C1 first pass:** no parameter-free center (NBM/GFS/ECMWF/HRRR/decorrelation blend) beats
  the market.
- **C1b (final, walk-forward, pre-registered):** **no conditioned center** (bias-corrected,
  inverse-MAE, regime-gated, obs-anchored) beats the market or reliably beats NBM-only.
- **C2 / EXP-2026-010 (lead-0 obs-timing nowcast, pre-registered):** the last edge-adjacent
  idea. An obs-anchored nowcast (metar-max-so-far + walk-forward remaining-rise) **loses to
  the market** in the held-out cohort (dBrier +0.0455, dRPS +0.0354; market wins in all 20
  stations, both sub-splits, both boundary cuts). The market already prices the live
  observation WeatherBot sees. See `EXP_C2_NOWCAST_RESULTS.md`.

This is a **forecast-information problem, not a trading problem**, and the available data
does not contain the missing information. **Pre-committed decision (EXP-C1b prereg §6):
recommend converting WeatherBot to observation-only analytics** (charter §7). Calendar
backstop: 2026-09-04 / 500 fresh station-days. **Final call is the operator's.**

## The single forward plan

➡️ **`docs/research/EXPERIMENT_PLAN_NEXT.md`** is the canonical plan and stopping rule.

**Agreed action order (auditor + analyst review, 2026-06-06):**

0. ✅ **DONE (2026-06-06)** — Lock the benchmark: coherent-snapshot is now the
   canonical selection in `research/market_relative_center_benchmark.py`
   (legacy behind `--selection latest_per_bucket`) + frozen regression test
   (`tests/test_market_relative_center_benchmark.py`, 9 tests passing).
1. ◑ **EXP-B1 in-sample experiment done (2026-06-06)** — METAR vs CLI floor: the
   **soft floor** (cap injected confidence) is the in-sample **candidate** — beats the
   production hard floor on all market-relative metrics and cuts `winner<5%` mass
   starvation 7→1, but it's **damage reduction only** (market gap stays +0.0837 Brier)
   and **not a validated fix**. Harness: `research/floor_basis_experiment.py` (pre-
   calibrator); results in `DEFECT_METAR_CLI_FLOOR.md §9`. Before any production change:
   production-like re-score (calibrator incl.) + walk-forward OOS validation. No
   production change made.
2. ◑ **EXP-B2 in-sample experiment done (2026-06-06)** — HRRR weight curve: the HRRR
   blend is **net-helpful** (turning it off is *worse*), correcting the earlier
   point-MAE-based "inferior model" overstatement. The curve is only mildly too
   aggressive at 13–16h (cap≤0.50/flat-0.30 ≈ −0.003 Brier); ≥17h high weight correct.
   **Keep the blend**; lower-mid-afternoon weight is a low-priority candidate, not
   validated. Harness: `research/hrrr_weight_experiment.py`; results in
   `DEFECT_HRRR_WEIGHT_CURVE.md §9`. Market gap unchanged.
3. ◑ **EXP-B3 in-sample experiment done (2026-06-06)** — GFS blend: false premise
   already corrected in code; EXP-B3 paired CI shows the 0.30 blend is **statistically
   indistinguishable** from NBM-only at lead-1 (ΔBrier +0.0015, CI [−0.0013,+0.0043]);
   **full GFS (w=1) significantly worse**. **Keep 0.30; no weight change** (no harm; high
   weights worse). Harness: `research/gfs_blend_experiment.py`; results in
   `DEFECT_GFS_BLEND.md §9`.
4. **← NEXT** — Calibrator — rebuild from all-forecasts-vs-CLI; restore ladder normalization.
5. Reliability metric — replace the dormant invalid metric.

**EXP-C1 — the decisive question: can any forecast center beat the market? → NO (both passes).**
- ✅ **C1 first pass (parameter-free):** no center (NBM/GFS/ECMWF/HRRR/decorrelation blend)
  beats the market at either lead. NBM-only is already the best center.
  (`research/center_market_benchmark.py`; `EXP_C1_FORECAST_CENTER_BENCHMARK.md`; EXP-2026-007.)
- ✅ **C1b (final, walk-forward, pre-registered, run on the VPS 2026-06-07):** no conditioned
  center (bias-corrected GFS/ECMWF/HRRR, inverse-MAE blend, |NBM−GFS| regime gate, lead-0
  obs-anchor) beats the market or reliably beats NBM-only. (`research/center_market_benchmark_wf.py`;
  `EXP_C1B_FORECAST_CENTER_WF.md`; `EXP_C1B_PREREGISTRATION.md`; EXP-2026-008.)

**DECISION (operator-approved 2026-06-07): pivot to observation-only analytics now** (charter
§7; C1b prereg §6). The 2026-09-04 / 500-fresh-station-day kill rule remains only as governance
confirmation — C1b was the last fair test. Trading logic unchanged (already paper-only); the
gate reopens only on genuinely new forecast information.

## Hard constraints (until the forecast gate is cleared)

- No change to production probabilities, sizing, or trade entry.
- No Kelly/TP/SL tuning, no new gates, no station whitelists, no ensemble promotion,
  no new production weather models.
- Promotion requires `docs/research/WEATHERBOT_PROMOTION_CRITERIA.md`: a forecast
  variant must beat **both** current WeatherBot **and** the market on Brier + RPS
  out of sample, with sufficient fresh station-days, no leakage.

## Document map (all in `docs/research/`)

| Doc | Purpose |
|---|---|
| `MARKET_BASELINE_THESIS.md` | Why this is a forecast-information problem (confirmed) |
| `MARKET_BASELINE_AUDIT.md` | Benchmark validity (confirmed, one correction) |
| `DEFECT_ANALYSIS_SUMMARY.md` | The 5 defects + agreed fix order |
| `DEFECT_METAR_CLI_FLOOR.md` / `DEFECT_HRRR_WEIGHT_CURVE.md` / `DEFECT_GFS_BLEND.md` | Wrong-direction model mechanics |
| `CALIBRATOR_REBUILD_REPORT.md` / `RELIABILITY_METRIC_REPORT.md` | Correctness/hygiene |
| `EXPERIMENT_PLAN_NEXT.md` | **Canonical forward plan + kill rule** |
| `WEATHERBOT_PROMOTION_CRITERIA.md` / `WEATHERBOT_RESEARCH_CHARTER.md` / `WEATHERBOT_EXPERIMENT_REGISTRY.md` | Governance |

Research-only harnesses built for the audit (no production behavior):
`research/snapshot_market_benchmark.py`, `research/floor_basis_diagnostic.py`,
`research/gfs_nbm_pit_center.py`.
