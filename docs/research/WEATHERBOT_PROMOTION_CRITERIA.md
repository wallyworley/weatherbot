# WeatherBot Promotion Criteria

**Purpose:** Define what must be true before research can affect probabilities, sizing, or trading.

---

## 1. Global Rule

No change may enter the trading path unless it first demonstrates market-relative forecast improvement out of sample.

---

## 2. Forecast Promotion Criteria

A forecast change may be promoted only if it improves:

- Brier Score versus current WeatherBot
- Brier Score versus market
- RPS versus current WeatherBot
- RPS versus market
- CRPS versus current WeatherBot
- Center MAE versus current WeatherBot

Preferred:

- Center MAE also beats market
- Reliability does not degrade
- Improvement holds across more than one station/regime

Minimum sample:

- 100 fresh station-days
- Preferred 250+

---

## 3. Calibration Promotion Criteria

Calibration may be promoted only if:

1. It is trained on all forecast records versus settlement truth.
2. It is walk-forward frozen.
3. It improves Brier or Log Loss.
4. It does not degrade RPS or CRPS.
5. It does not make market-relative scores worse.
6. It does not rely on the bot's own signal log.

Calibration cannot compensate for a forecast center that loses to the market.

---

## 4. Mechanical Fix Promotion Criteria

Mechanical fixes are allowed if they correct a known defect and do not degrade market-relative skill.

Examples:

- CLI-consistent floor basis
- Correct reliability metric
- Correct timestamp alignment
- Correct settlement mapping

Mechanical fixes do not require positive P&L but must not introduce leakage.

---

## 5. Trading Strategy Promotion Criteria

Blocked until forecast promotion criteria are met.

Once unblocked, a strategy change must show:

- Positive realized EV after fees
- Realistic fill assumptions
- No dependence on stale quotes
- Acceptable drawdown
- Positive results by side, station, and price band
- No single-station dependence

Minimum sample:

- 100 realistic simulated fills
- Preferred 250+

---

## 6. Station Promotion Criteria

A station may be active only if:

- Forecast skill is not materially worse than market after the relevant model changes.
- Realized EV is non-negative after fees and realistic fills.
- Calibration is acceptable.
- Sample is sufficient.

A station should be disabled if:

- It persistently loses to the market forecast.
- It has negative realized EV after sufficient sample.
- It requires in-sample tuning to appear profitable.

---

## 7. Ensemble Promotion Criteria

Ensemble logic remains shadow-only unless it beats:

- Current WeatherBot
- Market-implied forecast

on:

- Brier
- RPS
- CRPS

Out of sample.

Minimum sample:

- 100 settled station-days minimum
- Preferred 250+

---

## 8. Kill Criteria

Stop or archive the program if:

- No forecast variant beats market-relative RPS after 500 fresh station-days.
- No forecast variant beats market-relative Brier after 500 fresh station-days.
- Improvements only appear in small, unstable, or in-sample slices.
- Trading profitability requires ignoring market-relative forecast underperformance.

---

## 9. Forbidden Promotion Arguments

The following are not valid reasons to promote:

- It improved backtest P&L on a small sample.
- It improved hit rate but worsened scoring rules.
- It worked on one station only.
- It worked only after filtering many slices.
- It increased confidence without improving CRPS/RPS.
- It performed well on the same data used to design it.
