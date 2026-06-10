# WeatherBot Experiment Registry

**Purpose:** Pre-register experiments before implementation and prevent overfitting.

Every experiment must be added here before code changes are made.

---

## Experiment Template

```markdown
## EXP-YYYY-NNN — Title

**Status:** Proposed | Running | Complete | Rejected | Promoted
**Owner:**
**Date Opened:**
**Date Closed:**

### Hypothesis

### Why This Matters

### Data Required

### Code Areas

### Training Window

### Validation Window

### Minimum Sample Size

### Metrics

Forecast metrics:
- Brier Score
- RPS
- CRPS
- Log Loss
- Center MAE
- Reliability

Trading metrics, if allowed:
- Realized EV vs modeled EV
- Post-fee P&L
- Max drawdown
- Sharpe-like return/risk

### Market Baseline

Describe how market-implied probabilities are computed.

### Pass Criteria

### Failure Criteria

### Overfitting Risk

Low | Medium | High

### Leakage Controls

### Result

### Decision

### Follow-Up
```

---

## EXP-2026-001 — Market-Relative Benchmark Audit

**Status:** Complete (2026-06-06) — **CONFIRMED**
**Priority:** P0

### Result / Decision (2026-06-06)

Benchmark **confirmed** and reproduced. One methodological defect found (time-incoherent
"latest-per-bucket" selection, median ~9 h spread at lead 0) was corrected: the
**coherent-snapshot** selection is now canonical (`market_relative_center_benchmark.py`,
default) + frozen regression test. Direction unchanged under the correction (market
beats WeatherBot on Brier/RPS/CRPS/center; CRPS magnitude was overstated ~40%). Settlement
mapping exact, no leakage, 561/561 events single-winner. See `MARKET_BASELINE_AUDIT.md`.

### Hypothesis

The 2026-06-07 market-relative benchmark correctly shows that the market-implied forecast beats WeatherBot across station/lead groups.

### Why This Matters

If true, the project has no demonstrated forecast edge. All trading optimization should remain blocked.

### Data Required

- Stored WeatherBot fair probabilities
- Market bid/ask or midpoint snapshots
- Bucket ladders
- Settlement truth
- Station/date/lead timestamps

### Code Areas

- Research benchmark script
- Probability normalization
- Settlement mapping
- Scoring utilities

### Minimum Sample Size

Existing benchmark sample is acceptable for audit, but future promotion requires fresh OOS station-days.

### Metrics

- Brier
- RPS
- CRPS
- Center MAE
- Paired confidence intervals

### Pass Criteria

Benchmark methodology is reproducible and no leakage or pairing defect is found.

### Failure Criteria

Any issue invalidates the benchmark conclusion, including timestamp leakage, incomplete bucket normalization, or incorrect settlement mapping.

### Overfitting Risk

Low, if audit-only.

---

## EXP-2026-002 — METAR vs CLI Floor Basis

**Status:** Complete in-sample (2026-06-06) — **candidate; OOS pending**
**Priority:** P0

### Result / Decision (2026-06-06)

Confirmed wrong-direction defect: the hard METAR floor exceeds CLI truth on ~20% of
lead-0 events and literally truncates the winning bucket on ~2%. EXP-B1
(`research/floor_basis_experiment.py`, pre-calibrator) found the **soft floor**
(`soft_w0.50`, cap injected confidence) is the in-sample winner — beats the hard floor
on Brier/RPS/CRPS/center and cuts `winner<5%` starvation 7→1. δ-subtraction and floor-off
rejected. **Damage reduction only** (market gap stays +0.0837). **Candidate, not a
validated fix** — needs production-like re-score (calibrator) + walk-forward OOS, no
in-sample weight tuning. No production change. See `DEFECT_METAR_CLI_FLOOR.md §9`.

### Hypothesis

Using raw METAR observations for intraday floor/ceiling creates a basis mismatch versus CLI settlement and manufactures false edge.

### Code Areas

- `models/distribution.py`
- intraday floor/ceiling logic
- settlement observation utilities

### Metrics

- Brier
- RPS
- CRPS
- Afternoon TMAX cohort performance
- Number of buckets incorrectly zeroed

### Pass Criteria

CLI-consistent floor basis improves or neutralizes market-relative scores without introducing leakage.

### Overfitting Risk

Medium.

---

## EXP-2026-003 — HRRR Late-Day Weight Curve

**Status:** Completed / **Revised** (2026-06-06)
**Priority:** P0

### Result / Decision (2026-06-06)

**Hypothesis REJECTED as stated.** EXP-B2 (`research/hrrr_weight_experiment.py`,
distribution-level market-relative scoring — the correct test) shows the HRRR blend is
**net-helpful**: `w=0` (NBM-only) is *worse* overall (dBrier +0.0889→+0.1068), materially
at 15–16h and ≥17h (~flat at 13–14h). The earlier "overweights an inferior model"
read came from point-MAE and was incomplete (it missed error-decorrelation). **Decision:
keep the blend; w=0 rejected.** The only supported tweak is a lower mid-afternoon weight
(`cap_0.50`) = a small **Brier/RPS** damage-reduction candidate (~−0.003 Brier; CRPS
~flat-to-worse; `flat_0.30` rejected) — **in-sample candidate only**, low priority, not a
validated fix. Market gap unchanged (~+0.086). See `DEFECT_HRRR_WEIGHT_CURVE.md §9`.

### Hypothesis (original — now revised; see Result above)

The HRRR weight curve overweights HRRR late in the day and creates confident-wrong distributions.

### Code Areas

- `models/distribution.py`
- `_hrrr_blend_weight`
- distribution center blend

### Metrics

- CRPS by hour-to-settlement
- Brier by hour-to-settlement
- Center MAE by hour-to-settlement
- Market-relative RPS

### Pass Criteria

A revised or disabled HRRR curve improves market-relative scores out of sample.

### Overfitting Risk

High.

---

## EXP-2026-004 — GFS vs NBM Point-in-Time Re-Derivation

**Status:** Completed / **Revised** (2026-06-06)
**Priority:** P0

### Result / Decision (2026-06-06)

**Hypothesis CONFIRMED, but decision revised.** Under strict point-in-time alignment
GFS does **not** beat NBM standalone (NBM p50 1.57/1.65 vs GFS 1.82/2.18 at lead 0/1) —
the in-code "GFS beats NBM" claim was a source/alignment artifact, now **corrected in
code**. EXP-B3 (`research/gfs_blend_experiment.py`, distribution-level market-relative,
paired CIs) shows at lead-1 the 0.30 blend is **statistically indistinguishable** from
NBM-only (paired ΔBrier +0.0015, CI [−0.0013,+0.0043] includes 0 — point estimate
slightly favors the blend but within noise), while **full GFS (w=1) is significantly
worse** (ΔBrier +0.0170, CI excludes 0). **Decision: keep the 0.30 blend; no weight
change** (no established harm or benefit vs NBM-only; high weights worse). Market gap
unchanged. See `DEFECT_GFS_BLEND.md §9`.

### Hypothesis (original — confirmed re: standalone MAE; blend decision revised, see Result)

The claim that GFS beats NBM may be caused by point-in-time alignment or valid-time aggregation artifacts.

### Code Areas

- `models/distribution.py`
- GFS center blend
- forecast ingestion / timestamp alignment

### Metrics

- Point-in-time MAE
- CRPS
- RPS
- Market-relative score

### Pass Criteria

(Pre-registered) GFS blend remains only if it beats NBM and market-relative benchmarks
out of sample.

> **Reconciled with the realized decision (2026-06-06):** this pre-registered bar was
> *demote unless it beats NBM*. EXP-B3 instead found the 0.30 blend **statistically
> indistinguishable** from NBM-only (neither beats nor loses) and **full GFS worse**, so
> the operative rule became **"keep frozen on a no-harm basis"** rather than "demote
> unless it beats NBM." A weight *change* (up or down) is what now requires OOS evidence;
> the status-quo 0.30 is retained without it. See Result/Decision above and
> `DEFECT_GFS_BLEND.md §9`.

### Overfitting Risk

Medium.

---

## EXP-2026-005 — Calibrator Rebuild From All Forecasts

**Status:** Proposed
**Priority:** P0

### Hypothesis

The production calibrator is biased because it trains on the bot's own signal log rather than all forecasts versus settlement truth.

### Code Areas

- `strategy/probability_calibration.py`
- calibration data generation
- scoring utilities

### Metrics

- Reliability
- Brier
- Log Loss
- RPS
- Market-relative performance

### Pass Criteria

New calibrator improves forecast scoring out of sample and does not degrade market-relative skill.

### Overfitting Risk

High.

---

## EXP-2026-006 — Disagreement-Right-Vs-Wrong Classifier

**Status:** Proposed
**Priority:** P1

### Hypothesis

There exists an observable subset of market/model disagreements where WeatherBot is more likely correct than the market.

### Candidate Features

- Forecast update recency
- Forecast movement magnitude
- Market movement magnitude
- HRRR/NBM agreement
- Station residual regime
- Cross-venue confirmation where valid
- Time of day
- Lead day

### Metrics

- Conditional market-relative RPS
- Conditional Brier delta
- Realized EV only after forecast success

### Pass Criteria

A pre-registered subset beats the market out of sample.

### Overfitting Risk

Very high.

---

## EXP-2026-007 — Forecast-Center Market-Relative Benchmark (EXP-C1)

**Status:** First pass COMPLETE 2026-06-06 — **no center beats market**; EXP-C1b (walk-forward) pending
**Priority:** P1 (the decisive forecast-information question)

### Hypothesis

Some available forecast center (NBM, GFS, ECMWF, HRRR, multi-model decorrelation blend,
bias-adjusted, regime-conditioned, or CLI-obs-conditioned) beats the Kalshi
market-implied center out of sample on market-relative Brier and RPS.

### Why This Matters

This is the program's binding question (charter §3, Q3). B1–B3 showed mechanical center
tweaks do not create edge; C1 asks whether any *center* can. If no center beats the
market OOS, the kill rule applies (charter §7).

### Data / Code

- Coherent-snapshot benchmark events (lead 0–1), stored market mids, settlement truth.
- `research/center_market_benchmark.py` (research-only; rebuild NBM-only PIT, set center
  to each candidate keeping NBM shape, score market-relative).
- Centers from `det_forecast` (GFS/ECMWF/HRRR) + `prob_forecast` (NBM).

### Variants

- **First pass (parameter-free, no walk-forward needed):** nbm_only (baseline,
  bias-corrected), gfs_center, ecmwf_center, hrrr_center (lead-0), and a fixed
  multi-model decorrelation blend (e.g. 0.5·NBM + 0.25·GFS + 0.25·ECMWF).
- **Follow-on (require walk-forward — deferred):** bias-corrected deterministic centers,
  inverse-recent-MAE decorrelation weights, regime-conditioned weights, CLI-obs-anchored
  centers.

### Metrics

Market-relative Brier, RPS, CRPS, center MAE by lead; paired per-event CI vs market AND
vs nbm_only.

### Pass Criteria

A center reaches **positive market-relative RPS and Brier** (i.e. negative dBrier_vs_mkt
and dRPS_vs_mkt with paired CI excluding 0) on ≥100 fresh station-days (preferred
250–500), ≥2 stations, ≥2 regimes, walk-forward, no leakage.

### Failure / Kill

If no center variant clears the bar after 500 fresh station-days or 90 days (~2026-09-04),
the program converts to observation-only analytics (charter §7).

### Leakage Controls

Strict `run_time ≤ as_of` (= coherent-snapshot ts); truth = settlement only; any fitted
weight (follow-on) trained strictly on prior days (walk-forward).

### Overfitting Risk

Low for the parameter-free first pass (no fitted params). Medium–High for the fitted /
regime-conditioned follow-on — pre-register, walk-forward, no slice mining.

### Known Limitation

`station_bias` has only NBM rows, so GFS/ECMWF/HRRR centers are scored **raw**
(un-bias-corrected) while NBM is bias-corrected — matching production, but a possible
disadvantage to the deterministic centers. Bias-corrected deterministic centers are a
walk-forward follow-on.

### Result / Decision (first pass, 2026-06-06)

**No available parameter-free center beats the market** at either lead (all positive
market-relative Brier AND RPS, CIs in the wrong direction). NBM-only is the best center at
lead-1 (+0.0231 Brier vs market); GFS/ECMWF/HRRR/decorrelation-blend are all worse than or
equal to NBM at lead-1. HRRR is best at lead-0 (beats NBM there, consistent with EXP-B2)
but still loses to market by +0.094. See `EXP_C1_FORECAST_CENTER_BENCHMARK.md`. **Decision:**
the simple centers are exhausted with a clear negative; the remaining hope is the
walk-forward follow-on (EXP-C1b: bias-corrected / inverse-MAE-decorrelation /
regime-conditioned / obs-anchored centers). Realistic prior given B1–B3 + this: the kill
rule will be approached. No production change.

---

## EXP-2026-008 — Walk-Forward Conditioned Forecast Centers (EXP-C1b)

**Status:** COMPLETE 2026-06-07 — **NO variant passes; recommend observation-only pivot**
**Priority:** P1 (the charter's final OOS center test)

Full locked pre-registration: `EXP_C1B_PREREGISTRATION.md`. Results:
`EXP_C1B_FORECAST_CENTER_WF.md`. Harness `research/center_market_benchmark_wf.py`, run on the
VPS against the local DB (38 s).

### Result / Decision (2026-06-07)

**No variant passes.** All six conditioned centers (bias-corrected GFS/ECMWF/HRRR,
inverse-MAE blend, |NBM−GFS| regime gate, lead-0 obs-anchor) still **lose to the market** at
both leads (positive market-relative Brier+RPS, Bonferroni-6 CIs excluding 0), and **none
significantly beats NBM-only** (closest: invmae_blend_bc lead-1 and obs_anchor_l0 lead-0 —
point estimates slightly below NBM, CIs include 0, still lose to market). A lead-1
reconstruction-alignment bug was found and fixed on the VPS (lead-aligned trailing cutoffs);
lead-0 unaffected. ecmwf_bc lead-1 (n=95) is data-limited/inconclusive but loses anyway.
**Pre-committed decision (prereg §6): recommend converting WeatherBot to observation-only
analytics (charter §7).** Calendar backstop 2026-09-04 / 500 fresh station-days; final call is
the operator's. No production change.

---

## EXP-2026-009 — Market-Information Forensics Dataset

**Status:** Running
**Priority:** P1
**Date Opened:** 2026-06-07

### Hypothesis

Kalshi's near-settlement accuracy is explained by observable public information
or update timing, not by another generic WeatherBot forecast-center tweak.

### Why This Matters

The current market-relative evidence says WeatherBot does not beat the market on
Brier, RPS, CRPS, or center MAE. The next useful research question is what the
market is incorporating, and whether WeatherBot observes that information early
enough for a paper-only, out-of-sample signal.

### Data Required

- VPS PostgreSQL database as the authoritative source.
- Report execution on the VPS itself; do not SSH/tunnel data back to local for evidence collection.
- Kalshi market snapshots across all active fetch stations.
- WeatherBot fair probabilities as of each snapshot.
- METAR high-so-far and latest observation timestamp.
- CLI settlement values from `cli_obs`.
- DSM values where captured in the longitudinal research file.
- NBM/HRRR/GFS/ECMWF and official guidance centers available as of snapshot.
- Recent market movement before and after the snapshot.

### Code Areas

- `research/market_information_forensics.py`
- `jobs/market_information_forensics_report.py`
- `research/reports/market_information_forensics_*.csv`

### Training Window

Historical completed station-days already collected in the VPS database, with
the report run on the VPS itself.

### Validation Window

Fresh station-days after this registration. Candidate signals must be scored
only on data collected after the candidate is frozen.

### Minimum Sample Size

At least 100 fresh station-days for any candidate signal unless explicitly
labeled exploratory.

### Metrics

Forecast metrics:
- Market-relative Brier
- Market-relative RPS
- CRPS
- Center MAE
- Paired confidence intervals

Timing diagnostics:
- Observation-to-market reaction lag
- 1, 5, 10, 30, and 60 minute market-center moves
- Boundary-state cohorts

### Market Baseline

Market-implied probabilities are normalized Kalshi bid/ask midpoints over a
coherent station/date/snapshot bucket set.

### Pass Criteria

- Positive market-relative Brier and RPS improvement out of sample.
- Paired confidence interval excluding zero.
- At least 100 fresh station-days unless exploratory.
- Holds across at least two stations, or one station with a pre-registered
  station-specific rationale.
- No leakage.
- No production trading change.

### Failure Criteria

- Market movement is explainable only after WeatherBot could observe it.
- Candidate improvement disappears out of sample.
- Candidate depends on settlement, future observation, stale bucket stitching,
  or station/date selection after seeing results.

### Overfitting Risk

High.

### Leakage Controls

The dataset constrains forecasts and WeatherBot probabilities to records
available as of the market snapshot. Settlement values are included for scoring
only and must not be used as features. Current-day rows are excluded by default.

### Result

**Validation 2026-06-07:** code is correct and leakage-safe (5/5 unit tests; canonical
coherent-snapshot methodology; all features as-of the snapshot; settlement and future-price
columns are labels-only; climatology uses strictly-prior days).

**Performance (resolved, evidence-driven):** the first build enriched every raw
`market_snapshot` row before the Python window-dedup, so it never finished at scale. Fixes
applied: (a) `base` to `reps` CTE that windows/dedups representatives in SQL first, plus an
`eligible_windows` CTE (`COUNT(DISTINCT ticker) >= min_buckets`) before enrichment;
(b) climo cached by flooring snapshot time to the tick window; (c) index-prunable
local-midnight ranges replacing the functional `(valid_time/obs_time AT TIME ZONE tz)::date`
filters. That made it complete but it was still ~120 s/station-day, so I stopped guessing and
ran `EXPLAIN ANALYZE` on the VPS. The plan named one culprit: the `det_forecast` LATERAL's
correlated `MAX(run_time)` SubPlan executed **1,665,000 times** (~405 s of 408 s); metar,
nbm, guidance, and the market-move scans were all sub-millisecond. Fix: restructure the det
subquery to find the latest run per (rep, model) once via `ORDER BY run_time DESC LIMIT 1`
over an indexed `run_time` range, then `MAX` the day's values.

**Result: registered-scale is now feasible.** VPS timings: 1 station-day **15 s** (was ~126 s,
EXPLAIN run 408 s); 7-day 3-station lead-0/1 **149 s** (4,956 rows, 21 station-days);
~7 s/station-day, so 30-day all-station is ~75 min versus ~21 h before. Output validated
correct (det centers HRRR/GFS/ECMWF + NBM percentiles + guidance all populated). The 30-day
all-station build is running on the VPS to produce the first registered-scale artifact. A
`--explain` flag was added for future profiling. No production trading change.

### Decision

No candidate signal is promoted by the dataset build itself. Continue broad
collection, including KHOU/Houston; no trading-logic change.

---

## EXP-2026-010 — Lead-0 Obs-Timing Nowcast (EXP-C2 instance)

**Status:** Complete (2026-06-08) — **NO PASS / hypothesis REJECTED**
**Priority:** P1 (the last edge-adjacent test)

### Result / Decision (2026-06-08)

**Hypothesis REJECTED, decisively negative by point estimate and station consistency.** The
locked obs-anchored nowcast (`obs_anchor_dist` = metar-max-so-far + walk-forward Normal
remaining-rise) **loses to the market** in the pre-registered held-out cohort: dBrier
**+0.0455**, dRPS **+0.0354** (positive = market wins), on 6,630 held-out events / 20 stations.
The sign is market-winning in **all 20 stations** (station-level sign test p ~= 1e-6), **both**
chronological sub-splits, and **both** boundary cuts; the near-boundary slice (where a fresh
obs should be most decisive) loses by slightly more. Zero of the four substantive pass
criteria met. (Caveat: snapshot-level CIs are anti-conservative due to within-station-date
clustering, so the conclusion leans on magnitude + 20/20 station agreement, not CI width; the
climo runs rolling strictly-prior rather than frozen-on-design-split, which only helps the
nowcast. See `EXP_C2_NOWCAST_RESULTS.md` caveats.) The market has already priced the live
observation WeatherBot sees. Locked design: `EXP_C2_NOWCAST_PREREGISTRATION.md`. No leakage
(as-of features; chronological held-out; settlement scoring-only). **No production trading
change.**

**This closes the forecast-edge question.** Combined with EXP-2026-001 (benchmark audit
confirmed), EXP-B1 to B3 (floor/HRRR/GFS, damage-reduction only), and EXP-C1/C1b (no center
variant beats the market OOS), every avenue is exhausted. Per the locked decision rule,
WeatherBot is observation-only analytics (charter §7).

### Design (locked pre-registration)

Concrete instance of EXP-2026-006, scoped to the lead-0 observation-timing mechanism from the
EXP-2026-009 forensics dataset. ONE signal (`obs_anchor_dist`), ONE primary cohort (lead-0,
local hour 13-17, fresh METAR <=10 min). Pass = beats the MARKET on Brier AND RPS on a
chronological held-out split, paired CI excluding 0, >=100 cohort events, >=2 stations,
negative in >=2 stations and >=2 sub-splits, no leakage.

---

## EXP-2026-011 — Market Reaction Latency Audit

**Status:** Forward collection IN PROGRESS (since 2026-06-09); instrumentation live + report
tool built/validated. NOT a verdict. Evidence run targeted on or after 2026-06-23.
**Priority:** P1 (the one new axis after the accuracy question closed)

### Status (2026-06-09)

Instrumentation verified live on the VPS: `info_provenance` collecting genuine forward
`first_seen_at` (metar/kalshi_book/polymarket_book confirmed; model-run/cli on cadence). Report
tool `research/market_reaction_latency.py` built, unit-tested (pure onset core), deployed, and
validated end to end. First smoke (one partial day) is forward-collection-in-progress, not
evidence: metar 20 event-days (median lag -23 min, leans negative as the prior expected),
model_run 17 event-days, cli 0; all below the 100 event-day gate. Smoke exposed and fixed a
startup-backfill artifact (genuineness latency cap added) and surfaced two open items for codex:
model-run `official_ts` is the nominal cycle time not availability (channel needs a real
availability estimand), and cross-venue scoring needs a rules-verified Kalshi/Polymarket
same-station map. DSM not first-class instrumented. Status doc: `EXP_2026_011_RESULTS.md`.
Measurement only; no production trading change.

**Cross-venue update (2026-06-09 late):** collection defect found and fixed (fetcher was
polling the resolved May-16 KLGA/KORD events — zero usable channel data); A2 comparable map
expanded 2 -> 7 stations after rules verification (added KAUS/KSEA/KLAX/KHOU/KSFO; KDFW
excluded, PM settles KDAL Love Field). 100-event-day gate now ~2026-06-25 instead of late
July. See `EXP_2026_011_CROSS_VENUE_MAP_VERIFICATION.md`.

**Codex resolutions folded (2026-06-09):** amendments A1-A6 locked in the prereg. Scoring
updated: Option A model-run anchor (windowed onset on first_seen_at), genuineness caps
(60/480/360 min) + instrumentation-start cutoff, cross-venue same-station map (comparable
KATL/KMIA; excluded KNYC/KMDW/KDEN). Research-only Kalshi WebSocket book collector (A5) built,
fixed to the dollar-fp schema, and LIVE as a systemd service (`weatherbot-kalshi-ws.service`,
subscribe-only, deduped to top-of-book changes). Cross-venue scorer WIRED 2026-06-09
late (locked f2e7031 statistic; 16 unit tests; first end-to-end VPS run validated — 8
episodes, all left-censored as expected on day one). All four channels now report.
**A7 (operator-authorized 2026-06-10):** PM-side poll censoring reduced — poll timer 5 -> 2 min
and a research-only Polymarket CLOB WS collector (`weatherbot-polymarket-ws.service`,
`polymarket_ws_book_event`, top-of-book dedupe, no auth, subscribe-only) now anchors t0 at
genuine WS receipt; scorer prefers WS with polled fallback. Locked statistic/gate unchanged.
Evidence run on/after 2026-06-23.

Locked prereg: `EXP_2026_011_MARKET_REACTION_LATENCY_AUDIT.md`. A MEASUREMENT program, not a
trading project. Tests the latency axis (does Kalshi reprice AFTER WeatherBot first sees a
public-info event), distinct from the closed accuracy axis (EXP-C1/C1b/C2). Four locked
channels (METAR, model-run availability, CLI/DSM, cross-venue Polymarket lead). Missing piece
was `first_seen_at` provenance instrumentation; codex added the research-only
`info_provenance` table/migration and additive writes for live METAR, model-run, CLI,
Kalshi book, and Polymarket book capture. Genuine forward collection is mandatory for the
untested channels; historical backfills must not be treated as first-seen evidence. One lag
statistic per channel = distribution of (market reprice onset minus first_seen_at), with
0.10 F market-center material move, median lag >=2 minutes / >=60% positive-lag / >=100
event-days / >=2 stations required merely to open a separate strict paper-only signal prereg
(EXP-2026-012). No candidate on any channel closes the latency axis too. No production trading
change in this audit. Honest prior: likely negative (edge compressed; METAR leans negative
already; hobby-scale vs sharps), highest upside is cross-venue. Reuses the EXP-2026-009
backbone.

---

## EXP-2026-013 — Shadow-Ensemble Market-Relative Benchmark

**Status:** Complete (2026-06-09) — **NO PASS; "genuinely new models" trigger consumed**
**Priority:** P1 (consumes the "genuinely new models" reopening trigger)
**Date Opened:** 2026-06-09

### Hypothesis

One of the four never-scored shadow ensembles (WEATHERNEXT2, ECMWF_AIFS_ENS, ECMWF_IFS_ENS,
GFS_ENS in `ensemble_forecast`, collected since 2026-05-10/15) beats the Kalshi
market-implied distribution as a center, bias-corrected center, or member-frequency
distribution.

### Why This Matters

The C1/C1b/C2 closure named "genuinely new models" as the only reopening trigger; the
2026-06-09 review found 36.9M ensemble rows (incl. two AI models absent from C1/C1b) that
were never benchmarked. This either reopens the axis or closes the documented gap.

### Design

Locked prereg: `EXP_2026_013_ENSEMBLE_MARKET_BENCHMARK.md`. Canonical coherent-snapshot
events, leads 0-1, TMAX_DAILY; twelve locked variants (center / walk-forward bias-corrected
center / Laplace-0.5 member-frequency dist, per model); as-of by `ingested_at <= snapshot_ts`
(run_time untrusted); harness `research/ensemble_market_benchmark.py` reusing the canonical
scoring. Pass = negative market-relative Brier AND RPS, paired CI excluding 0, n>=100,
>=2 stations. Candidate only earns a fresh-forward prereg; no production change either way.

### Overfitting Risk

Low (parameter-free first pass; bc variant walk-forward; no smoothing/weight search).

### Result / Decision (2026-06-09)

**No variant beats the market** (578 events, 19 stations, 2026-05-10..06-08): all twelve
variants positive market-relative Brier AND RPS at both leads, CIs excluding 0 the wrong
way. Notable for the record: **WEATHERNEXT2_center_bc at lead-1 is the first variant ever
to beat the NBM baseline** (paired dRPS -0.0152, CI [-0.0287,-0.0017]; market gap narrowed
to +0.0127 vs NBM's +0.0231) — but it still loses to the market, so no candidate. Raw WN2
is crippled by 6-hourly sampling (~1.3F center penalty); hourly WN2-class data would be
genuinely new and could be pre-registered fresh. Accuracy axis stays closed.
See `EXP_2026_013_RESULTS.md`. No production change.

---

## EXP-2026-014 — Kalshi Market Self-Calibration (Favorite-Longshot Bias)

**Status:** Complete (2026-06-09) — **DESIGN FAIL; axis closed**
**Priority:** P1 (the one structural axis never tested; does not require beating the market)
**Date Opened:** 2026-06-09

### Hypothesis

Kalshi morning prices exhibit favorite-longshot bias large enough that buying the morning
favorite (highest-mid bucket, mid >= 0.50, at the ask, taker fees included) has positive
expected value held to settlement — with no forecast input at all.

### Why This Matters

Every closed experiment tested WeatherBot-vs-market forecast skill. This tests whether the
market's own price structure leaks money. Disclosed peek: one exploratory decile query
(2026-06-09 review) motivated it; therefore history = design set only, and a pre-committed
forward window (>=300 fresh events, valid_date >= 2026-06-10) is the actual test.

### Design

Locked prereg: `EXP_2026_014_MARKET_SELF_CALIBRATION.md`. Fixed 14-16 UTC reference snapshot;
one locked primary rule; executable prices + Kalshi taker fee; cluster bootstrap by
station-date; TMAX+TMIN. Harness `research/market_longshot_bias.py`. Design pass requires
CI excluding 0 AND both chronological halves positive AND >=60% of stations positive; pass
only opens the forward window. Nothing trades.

### Overfitting Risk

Medium (one disclosed peek; mitigated by single locked rule + forward window).

### Result / Decision (2026-06-09)

**Zero of three design-pass criteria met** (195 events): net EV +0.0083/contract, cluster
CI [-0.0564,+0.0753] includes 0; second half negative (-0.0043); 4/7 stations (57%) < 60%.
The favorite-longshot SHAPE is real (longshot decile 0.2-0.3 edge -0.0602, CI excludes 0;
favorite point estimates positive) but ~80% of it is consumed by spread + taker fees: the
exploratory +5-7pp was a mid-price fee-free artifact. Per locked rule the axis closes; no
forward window. Maker-side capture would be separate execution research, out of scope.
See `EXP_2026_014_RESULTS.md`. No production change; nothing traded.

---

## EXP-2026-015 — Venue-Wide Kalshi Settlement-Calibration Sweep

**Status:** Registered + LOCKED 2026-06-10; backfill running
**Priority:** P1 (operator-directed search for structural edge beyond weather)
**Date Opened:** 2026-06-10

### Hypothesis

Some (category x price-band) cell of the Kalshi settled-market universe shows settlement
frequency deviating from executable settlement-eve prices by more than spread + taker fees,
stably across two independent chronological halves. Retail corners (PARLAY/KXMVE,
Entertainment, Mentions, long-dated event markets) are the priors-favored cells.

### Why This Matters

The weather program closed negative on accuracy and (weather-only) price structure. This
generalizes the one structural question that needs no forecast — is the venue itself
calibrated at executable prices — to all ~15 categories. Recon 2026-06-10: ~10,800 series;
settled markets carry result; daily candlesticks give per-market executable yes bid/ask.

### Design

Locked prereg: `EXP_2026_015_VENUE_CALIBRATION_SWEEP.md`. 90-day settled census
(`kalshi_settled_market`), settlement-eve daily-candle reference (volume >= 500, life >= 1
day), locked grid (16 categories x 7 bands x 2 sides), taker fees, cluster bootstrap by
event_ticker, candidate requires BOTH chronological halves independently (n>=50, CI excl 0,
edge > 1c). Sweep-wide false-positive expectation < 1 cell by construction. Candidate only
earns a forward-window prereg (>=200 fresh markets); no candidate closes the venue-structure
axis. Harness `research/kalshi_settled_calibration.py`. Nothing trades.

### Overfitting Risk

High by construction (a sweep); mitigated by the locked grid + dual-half rule + forward window.

### Result

Pending (backfill + candle fetch in progress).
