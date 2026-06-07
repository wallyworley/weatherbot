# Market Baseline Thesis

**Date:** 2026-06-07
**Status:** Canonical research input
**Trading status:** Paper only / no live promotion

---

## 1. Current Thesis

The current evidence indicates that WeatherBot does **not** have a demonstrated forecasting edge versus the market.

The 2026-06-07 market-relative benchmark found that the market beat WeatherBot across every scored station/lead group and across all primary forecast metrics:

- Brier Score
- Ranked Probability Score (RPS)
- CRPS
- Center MAE

This changes the project from a trading-strategy optimization problem into a forecast-information validation problem.

The central question is no longer:

> How do we trade WeatherBot signals better?

The central question is:

> Can WeatherBot produce a forecast distribution that beats the market-implied distribution out of sample?

---

## 2. Governing Interpretation

If the market forecast beats WeatherBot's forecast center and distribution, then:

- Kelly sizing cannot create edge.
- Calibration cannot create edge from an inferior forecast center.
- Price-band filtering cannot create durable edge unless it identifies a market-mispricing subset.
- Station whitelists are likely overfit unless validated out of sample.
- Execution improvements can only reduce losses; they cannot create forecast information advantage.

Therefore, no trading logic should be promoted until WeatherBot demonstrates market-relative forecast skill.

---

## 3. Required Benchmark Audit

Before making this document fully canonical, the benchmark methodology must be audited.

Audit questions:

1. Were WeatherBot probabilities and market midpoint probabilities normalized over exactly the same bucket set?
2. Were bucket sets complete enough to represent the full settlement distribution?
3. Was only information available at the forecast timestamp used?
4. Was there any timestamp leakage from later market prices?
5. Were settlements mapped correctly to buckets?
6. Were market midpoint probabilities fee-free and comparable to model fair probabilities?
7. Were sparse stations or partial bucket ladders handled consistently?
8. Were lead-day and station groupings paired on identical events?
9. Were confidence intervals computed from event-level paired deltas?
10. Were CRPS bucket-center approximations appropriate for bucket width?

If the benchmark survives this audit, it becomes the primary proof that the bot currently lacks market-relative forecast edge.

---

## 4. Burden of Proof

Every proposed model, calibration, or strategy change must answer:

> Does this improve market-relative forecast skill out of sample?

Minimum metrics:

- Brier Score
- RPS
- CRPS
- Center MAE
- Reliability by bucket/probability band

Trading metrics are secondary until forecast skill is demonstrated.

---

## 5. Promotion Rule

No change may affect live probabilities, sizing, or trade entry unless it first demonstrates:

1. Out-of-sample improvement versus the current WeatherBot forecast.
2. Out-of-sample improvement versus the market-implied forecast.
3. Sufficient sample size by station/lead/regime.
4. No evidence of future leakage.
5. Robustness across at least two independent segments, such as stations, regimes, or lead days.

---

## 6. Program Kill Rule

The project should pre-register a stopping rule.

Suggested rule:

If no forecast-center variant beats the market-implied forecast by positive market-relative RPS and Brier across at least 500 fresh station-days or 90 calendar days, the project should be archived or reduced to observation-only research.

---

## 7. Implications

The following work is now lower priority:

- Advanced Kelly sizing
- Stop-loss/take-profit tuning
- Station whitelists
- Price-band optimization
- Ensemble promotion
- New live strategy gates

The following work is now highest priority:

- Benchmark methodology audit
- Forecast-center improvement
- Mechanical defect audit
- Market-relative scoring harness
- Disagreement-right-vs-wrong classifier

---

## 8. Standing Rule

In-sample profitability never justifies deployment.

In-sample results may only justify hypotheses for walk-forward testing.
