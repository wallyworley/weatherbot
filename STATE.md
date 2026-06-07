# WeatherBot — Current State

**Updated:** 2026-06-06
**Trading status:** **Paper only. No live promotion.**
**Program status:** Forecast-information research (not trading optimization).

---

## Where we are

The 2026-06-07 market-relative benchmark was audited (2026-06-06) and **confirmed**.
The stronger, evidence-backed statement now stands:

> **WeatherBot's forecast distribution is materially worse than the Kalshi
> market-implied distribution** on Brier, RPS, CRPS and center MAE — even after
> correcting the benchmark's biggest methodological flaw.

This is a **forecast-information problem, not a trading problem.** The binding
constraint is the forecast center / resolution. The full production stack is
neutral-to-negative versus clean NBM, and clean NBM still loses to the market, so
mechanical fixes can reduce self-inflicted damage but are unlikely to create edge.

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

Running continuously: **can any forecast center beat the market out of sample?**
(EXP-C1). Kill rule: if no center clears positive market-relative **RPS and Brier**
OOS within **500 fresh station-days or 90 days (~2026-09-04)**, convert WeatherBot to
**observation-only analytics**.

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
