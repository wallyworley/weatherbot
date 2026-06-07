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

**Status:** Proposed
**Priority:** P0

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

**Status:** Proposed
**Priority:** P0

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

**Status:** Proposed
**Priority:** P0

### Hypothesis

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

**Status:** Proposed
**Priority:** P0

### Hypothesis

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

GFS blend remains only if it beats NBM and market-relative benchmarks out of sample.

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
