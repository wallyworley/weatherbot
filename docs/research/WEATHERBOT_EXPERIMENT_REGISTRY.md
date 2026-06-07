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
