# WeatherBot — Current State

**Updated:** 2026-08-04

**Program status: RETIRED.** The project was retired for not making money. Collectors were
stopped 2026-07-02 23:03 UTC and the `weather_bot` database has been dropped.

**2026-08-04 addendum: the last open axis is closed.** **EXP-2026-011** (market reaction
latency) was scored against its locked §7 gate: **no candidate on any channel.** Channels 1
(METAR, −10.56 min / 28% positive) and 2 (model-run, −16.25 min / 27%) are negative at full
pre-registered power over 299 event-days and 20 stations. Channel 2 is the decisive one: it
carried the "speed on model reads" thesis, and the market reprices a new model run a median of
16 minutes *before* our ingest sees it. Channel 3 (CLI) is negative on direction with an
arithmetically unreachable gate (0 positive lags in 86 events). Channel 4 (cross-venue
Polymarket) is **terminated unscored** at 14 of 100 required event-days, not scored negative.

Two honest caveats on this closure. The intended fuller evidence run could not be produced: the
~9 days of collection from 2026-06-23 to 2026-07-02 were never scored and the source data is
destroyed (the only surviving dump, 2026-05-09, predates the instrumentation). Estimated effect
is nil: CLI would have cleared its sample gate while still failing on 0% positive-lag, and
cross-venue would have reached only ~23 event-days. And polling censoring biases measured lag
*positive*, so the true lags are at least as negative as measured, making the negative findings
conservative. See `EXP_2026_011_RESULTS.md` and the preserved evidence run
`EXP_2026_011_EVIDENCE_RUN_2026-06-23.md`.

**Both axes are now closed: accuracy (EXP-C1/C1b/C2, EXP-2026-013) and latency (EXP-2026-011),
plus venue structure (EXP-2026-014/015). No open research questions remain.**

**2026-06-09 addendum:** two further pre-registered axes ran and closed negative the same
day. **EXP-2026-013** (the four never-benchmarked shadow ensembles incl. WeatherNext 2 and
AIFS — the "genuinely new models" reopening trigger): no variant beats the market; trigger
consumed. (For the record: bias-corrected WN2 at lead-1 is the first variant ever to beat
the NBM baseline, market gap narrowed ~45%, but the market still wins.) **EXP-2026-014**
(Kalshi favorite-longshot self-calibration, a market-structure axis independent of forecast
skill): the bias is real but ~80% is consumed by spread + taker fees; design fail on all
three locked criteria; axis closed. See `EXP_2026_013_RESULTS.md` / `EXP_2026_014_RESULTS.md`.
The only open axis remains **EXP-2026-011** (reaction latency; evidence run ≥ 2026-06-23).

**2026-06-10 addendum:** the operator-directed venue-wide search also closed negative.
**EXP-2026-015** (settlement-calibration sweep across ALL Kalshi categories: 7.0M-market
90-day census, 12,915 sampled at settlement-eve executable prices, fees in, dual-half rule):
**zero candidates**. Most cells lose on BOTH sides (spread+fee envelope); parlays are quoted
too wide to harvest (buy-YES EV −0.80/contract); the favorite-longshot lean replicates
venue-wide but is cost-eaten everywhere (third independent measurement). The venue-structure
axis is closed. See `EXP_2026_015_RESULTS.md`. ~~Remaining open question: EXP-2026-011 only.~~
(Closed 2026-08-04, see the addendum at the top of this file.)
**Trading status:** **Paper only. No live promotion. Retired 2026-07-02; never traded live.**
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

## The single forward plan (historical, superseded by retirement)

> **Spent.** Everything below is the plan as it stood on 2026-06-06 and is kept for the record.
> The program is retired and no item here is live. Items 4 and 5 were never done, and the
> "← NEXT" marker on item 4 is historical, not an open action.

➡️ **`docs/research/EXPERIMENT_PLAN_NEXT.md`** was the canonical plan and stopping rule.

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
4. ✗ **NOT DONE (program retired)** Calibrator: rebuild from all-forecasts-vs-CLI; restore
   ladder normalization.
5. ✗ **NOT DONE (program retired)** Reliability metric: replace the dormant invalid metric.

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
| `EXPERIMENT_PLAN_NEXT.md` | Forward plan + kill rule (spent; program retired) |
| `EXP_2026_011_RESULTS.md` | **Latency axis final scoring — the last axis, closed** |
| `EXP_2026_011_EVIDENCE_RUN_2026-06-23.md` | Sole surviving machine-generated latency run |
| `WEATHERBOT_PROMOTION_CRITERIA.md` / `WEATHERBOT_RESEARCH_CHARTER.md` / `WEATHERBOT_EXPERIMENT_REGISTRY.md` | Governance |

Research-only harnesses built for the audit (no production behavior):
`research/snapshot_market_benchmark.py`, `research/floor_basis_diagnostic.py`,
`research/gfs_nbm_pit_center.py`.
