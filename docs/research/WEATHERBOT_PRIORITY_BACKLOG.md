# WeatherBot Priority Backlog

**Date:** 2026-06-07
**Purpose:** Repo-ready implementation backlog after market-relative benchmark.

---

## Epic 0 — Freeze and Protect

### Story 0.1 — Preserve Current Baseline

**Priority:** P0

Tasks:

- Commit current repo state.
- Add current benchmark report to docs.
- Record current active parameters.
- Ensure paper mode remains enabled.
- Confirm no live trading changes are made.

Acceptance Criteria:

- Baseline commit exists.
- Benchmark report is committed.
- Research docs are committed.

---

## Epic 1 — Market Baseline Audit

### Story 1.1 — Audit Market-Relative Benchmark

**Priority:** P0

Tasks:

- Verify WeatherBot and market probabilities are normalized over identical bucket sets.
- Verify event pairing by station/date/lead.
- Verify settlement mapping.
- Verify no timestamp leakage.
- Verify midpoint calculation.
- Verify CRPS/RPS implementation.
- Reproduce summary table.

Acceptance Criteria:

- `MARKET_BASELINE_AUDIT.md` created.
- Audit either confirms benchmark or identifies defects.
- Any defects are listed with severity and fix plan.

---

### Story 1.2 — Add Benchmark Regression Fixture

**Priority:** P0

Tasks:

- Create small frozen settled-signals fixture.
- Include bucket probabilities, market mids, settlement, station/date/lead.
- Add test that reproduces benchmark metrics on fixture.

Acceptance Criteria:

- CI or local test catches scoring regressions.

---

## Epic 2 — Mechanical Defect Audit

### Story 2.1 — METAR vs CLI Floor Basis

**Priority:** P0

Tasks:

- Identify floor/ceiling observation source.
- Compare METAR floor to CLI settlement basis.
- Count cases where METAR floor would eliminate valid settlement buckets.
- Implement research-only alternative floor basis.

Acceptance Criteria:

- `DEFECT_METAR_CLI_FLOOR.md` created.
- Recommendation: fix, disable, or keep.

---

### Story 2.2 — HRRR Weight Curve

**Priority:** P0

Tasks:

- Reconstruct HRRR weight by local hour.
- Score current HRRR blend versus NBM-only and alternative weights.
- Segment by station, hour, and lead.

Acceptance Criteria:

- `DEFECT_HRRR_WEIGHT_CURVE.md` created.
- HRRR curve is either supported or demoted to research-only.

---

### Story 2.3 — GFS Center Blend

**Priority:** P0

Tasks:

- Re-derive GFS vs NBM under strict point-in-time alignment.
- Verify valid-time aggregation.
- Score GFS blend versus NBM and market.

Acceptance Criteria:

- `DEFECT_GFS_BLEND.md` created.
- Production GFS weight is either justified or demoted.

---

## Epic 3 — Calibration and Metrics

### Story 3.1 — Fix Reliability Metric

**Priority:** P0

Tasks:

- Replace proxy reliability output with true predicted-vs-observed reliability curve.
- Bin by forecast probability.
- Support bucket-level reliability.
- Add tests.

Acceptance Criteria:

- `verification/metrics.py` produces valid reliability diagrams.

---

### Story 3.2 — Rebuild Calibrator From All Forecasts

**Priority:** P0

Tasks:

- Stop using signal log as calibration source.
- Build all-forecasts-vs-CLI calibration table.
- Implement walk-forward frozen calibration maps.
- Compare current calibrator vs rebuilt calibrator.

Acceptance Criteria:

- `CALIBRATOR_REBUILD_REPORT.md` created.
- New calibrator promoted only if market-relative scores improve.

---

## Epic 4 — Forecast Information Program

### Story 4.1 — Morning Forecast Center Benchmark

**Priority:** P1

Tasks:

- Score morning forecast center versus market by station/lead.
- Include Brier, RPS, CRPS, center MAE.
- Use only timestamp-valid data.

Acceptance Criteria:

- `MORNING_FORECAST_CENTER_BENCHMARK.md` created.

---

### Story 4.2 — Disagreement Classifier Research

**Priority:** P1

Tasks:

- Build dataset of market/model disagreement events.
- Label whether WeatherBot or market was closer.
- Evaluate candidate signal-time features.
- Do not deploy.

Acceptance Criteria:

- `DISAGREEMENT_CLASSIFIER_RESEARCH.md` created.
- Any claimed subset has OOS validation.

---

## Epic 5 — Program Governance

### Story 5.1 — Add Program Kill Rule

**Priority:** P0

Tasks:

- Add kill criteria to STATE.md or research charter.
- Define station-day target.
- Define research end date.
- Define what archive/observation-only means.

Acceptance Criteria:

- Program stopping rule is explicit and committed.

---

## Blocked Work

Do not prioritize until forecast edge exists:

- Kelly tuning
- Stop-loss optimization
- Take-profit optimization
- Station whitelist
- Price-band trading filters
- New live trading strategies
- Real-money deployment
