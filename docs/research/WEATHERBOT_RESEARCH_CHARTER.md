# WeatherBot Research Charter

**Date:** 2026-06-07
**Purpose:** Define the scientific research program for WeatherBot after the market-relative benchmark.

---

## 1. Mission

Determine whether WeatherBot can produce a statistically defensible forecast-information advantage versus Kalshi market-implied probabilities in daily temperature markets.

This is a forecast-information project first.

It is not currently a trading-optimization project.

---

## 2. Current Status

The bot has no demonstrated edge.

The strongest current evidence indicates:

- The market-implied forecast beats WeatherBot on Brier Score.
- The market-implied forecast beats WeatherBot on RPS.
- The market-implied forecast beats WeatherBot on CRPS.
- The market-implied center beats WeatherBot center MAE.
- No station/lead group has yet demonstrated WeatherBot superiority in the benchmark.
- Mechanical issues may be manufacturing false edge.
- Calibration should not be treated as the primary problem unless the benchmark audit proves otherwise.

---

## 3. Primary Research Question

Can WeatherBot build a forecast distribution that beats the market-implied forecast distribution out of sample?

---

## 4. Secondary Research Questions

### Q1 — Benchmark Validity

Is the market-relative benchmark methodologically correct?

### Q2 — Mechanical Defects

Are current model losses partly caused by known mechanical defects?

Known candidates:

- METAR vs CLI floor basis mismatch
- HRRR late-day weight curve
- GFS center blend
- Reliability metric defect
- Signal-log calibrator bias

### Q3 — Forecast Center

Can any forecast center beat the market-implied center?

Candidate sources:

- NBM
- HRRR
- GFS
- ECMWF/Open-Meteo
- CLI-consistent observation conditioning
- Bias-adjusted variants
- Regime-conditioned variants

### Q4 — Disagreement Classification

When WeatherBot disagrees with the market, can any observable signal identify when WeatherBot is more likely correct?

Potential features:

- Forecast update recency
- HRRR/NBM disagreement
- Market movement without forecast movement
- Forecast movement without market movement
- Cross-venue agreement where valid
- Regime features
- Station-specific residual structure

### Q5 — Calibration

After correcting benchmark and mechanical issues, does calibration improve market-relative forecast skill?

Calibration must be trained on all forecasts versus settlement truth, not the bot's own signal log.

---

## 5. Research Principles

1. Forecast skill comes before trading profitability.
2. Market-relative skill is the benchmark.
3. No in-sample result may enter the paper/live trading path.
4. All tests must be walk-forward.
5. No future data may be used.
6. Forecast accuracy and trading profitability must be evaluated separately.
7. No ensemble logic may be promoted unless it beats production and market probabilities out of sample.
8. No strategy gate may be promoted unless it identifies a correct-disagreement subset, not merely a historically profitable slice.
9. If evidence is insufficient, the correct conclusion is "insufficient evidence."
10. Paper mode remains mandatory until forecast and execution criteria are both met.

---

## 6. Program Success Bar

A WeatherBot forecast variant clears the research bar only if it demonstrates:

- Positive market-relative RPS.
- Positive market-relative Brier.
- Non-degraded CRPS.
- Non-degraded center MAE.
- Walk-forward out-of-sample validation.
- At least 100 fresh station-days minimum.
- Preferred 250 to 500 fresh station-days.
- Results across at least two stations and two regimes.

---

## 7. Program Kill Bar

If no forecast variant clears the success bar after either:

- 500 additional station-days, or
- 90 calendar days of research,

then WeatherBot should be archived, paused, or converted to observation-only analytics.

---

## 8. Current Priority Order

### P0 — Audit Before Building

1. Audit market-relative benchmark methodology.
2. Verify bucket normalization and event pairing.
3. Verify no timestamp leakage.
4. Verify settlement mapping.
5. Verify CRPS/RPS implementations.

### P0 — Mechanical Defects

6. Fix or validate METAR vs CLI floor basis.
7. Validate HRRR late-day weight curve.
8. Re-derive GFS vs NBM claim under strict point-in-time alignment.
9. Rebuild calibrator using all forecasts versus settlement truth.
10. Replace or delete the defective reliability metric.

### P1 — Forecast Information

11. Build market-relative morning forecast benchmark.
12. Score forecast centers against market by station/lead/regime.
13. Build disagreement-right-vs-wrong research harness.

### P2 — Trading Research

Trading research is blocked until forecast-information advantage exists.

---

## 9. Non-Goals

Do not prioritize:

- New trading strategies
- Kelly tuning
- Take-profit tuning
- Stop-loss optimization
- Station whitelists
- Price-band backtest mining
- Ensemble promotion
- Live trading

unless the forecast-information gate is cleared first.

---

## 10. Operating Rule

If WeatherBot cannot beat the market forecast, WeatherBot should not trade against the market.
