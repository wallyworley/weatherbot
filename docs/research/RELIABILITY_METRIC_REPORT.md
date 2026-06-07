# Reliability Metric Report

**Date:** 2026-06-06
**Defect class:** Invalid metric implementation
**Severity:** Medium for correctness; **Low** operationally (the broken metric is dormant)
**Recommendation:** Replace the `verification/metrics.py` reliability output with a
true predicted-vs-observed reliability curve (the dashboard already has a correct
implementation to port); relabel the dashboard "raw" calibration chart.

---

## 1. Files and functions inspected

- `verification/metrics.py`
  - `run(lookback_days)` lines 120–132 — the reliability construction
  - `cdf_to_bucket_probs`, `brier_bucket`, `crps`, `log_loss_bucket` (the other,
    correct, metrics in this module)
- `jobs/nightly_verify.py` — the only caller of `verification.metrics.run`
- `dashboard/queries.py`
  - `reliability_bins`, `event_reliability_bins`, `yes_probability_calibration`
    (the live dashboard reliability — separate implementations)
- DB table `verification` (freshness check)

## 2. The defect (verification/metrics.py)

The "reliability diagram" is computed as:

```python
arr_pa = prob assigned to the OBSERVED bucket, per sample
bin_idx = digitize(arr_pa)              # bin by prob-assigned-to-observed
reliability[bin] = {
    "mean_forecast_prob": arr_pa[mask].mean(),
    "empirical_freq":     mask.sum() / len(samples),   # <-- BUG
    "n":                  mask.sum(),
}
```

Two compounding errors make this **not a reliability metric at all**:

1. **`empirical_freq` is a histogram density, not an outcome frequency.**
   `mask.sum()/len(samples)` is the fraction of samples whose prob-assigned falls
   in the bin. A reliability diagram needs "of the forecasts that said p≈X, what
   fraction actually occurred?" — i.e. the mean realized outcome in the bin, not the
   bin's share of the sample.
2. **The conditioning event is degenerate.** It bins by the probability assigned to
   the *observed* bucket, but the corresponding outcome ("did the observed bucket
   occur?") is **true by construction**. A correct reliability over this conditioning
   would be 1.0 in every bin; this code instead reports the histogram. There are no
   non-events, so reliability cannot be measured this way.

The code comment admits it: *"(a rough proxy; a proper reliability diagram requires
per-bucket pairs)."* Decisions phrased as "the bot is X% overconfident" must **not**
cite this output.

## 3. Data used and sample size

- `verification` table freshness: **last `run_date` 2026-04-20, 4 rows, 1 station.**
  The job is effectively **dormant** — it is not producing current reliability and
  nothing live consumes it. Operational risk today is low; the risk is that someone
  re-enables it and trusts the number.
- The model's actual overconfidence assessments in project history came from a
  **different** harness (`research/calibration_mark_to_settlement.py`), not this one.

## 4. The live dashboard reliability is correct (with one labeling issue)

`dashboard/queries.py` computes reliability properly:

- `reliability_bins`: `WIDTH_BUCKET(p_side)` then `AVG(won)` — real observed win
  rate per predicted-probability decile. Correct.
- `event_reliability_bins`: same, event-weighted (de-duplicated per ticker/side/bin).
  Correct.
- `yes_probability_calibration`: bins raw YES bucket prob, observes bucket-settle
  rate. Correct in form.

Caveat (ties to `CALIBRATOR_REBUILD_REPORT.md`): all three bin by `s.fair_prob`,
which is the **calibrated** value, while `yes_probability_calibration`'s docstring
calls it "raw YES bucket probability." So the live charts measure
**post-calibration residual reliability on the signal-log/fill-conditioned sample**,
not the raw model's reliability on all forecasts. They are valid for what they
measure but are mislabeled and inherit the signal-log selection.

## 5. Exact conclusion

`verification/metrics.py`'s reliability output is **invalid** (`empirical_freq` is a
histogram density over a degenerate conditioning event) and should be replaced or
deleted. It is currently dormant, so the operational impact is low, but it would
mislead if re-run. The live dashboard reliability functions are **methodologically
correct** but (a) measure calibrated, not raw, probabilities and (b) are
signal-log/fill conditioned — both should be relabeled and, for promotion
decisions, replaced by an all-forecasts raw-probability reliability curve.

## 6. Statistical limitations

- The dormancy finding is a point-in-time DB snapshot; if `nightly_verify` is
  re-scheduled it will start emitting the invalid metric again.
- The dashboard reliability charts' sample is whatever the bot traded/ticked; small
  per-station bins are noisy (the `HAVING n ≥ 2` / `≥ 1` thresholds are very low).

## 7. Overfitting risk

**None for the metric fix itself** (it is a correctness change, not a fitted model).
Downstream calibration built on a corrected reliability curve carries the
overfitting risk discussed in `CALIBRATOR_REBUILD_REPORT.md`.

## 8. Recommended next step

1. Replace `verification/metrics.py` reliability with a true predicted-vs-observed
   curve: per bucket, bin by forecast probability, observe the realized 0/1 outcome,
   report mean forecast prob and mean outcome per bin (port the dashboard's
   `event_reliability_bins` logic). Add a unit test on a tiny fixture.
2. Relabel the dashboard "raw YES" calibration as "calibrated YES," and add a true
   **raw**-probability reliability curve for promotion decisions.
3. Either re-enable `nightly_verify` with the corrected metric or remove the dead job.
