# Calibrator Rebuild Report (Signal-Log Bias Audit)

**Date:** 2026-06-06
**Defect class:** Circular training source + normalization break
**Severity:** Medium for correctness; **Low** for the market gap (net effect is tiny)
**Recommendation:** Rebuild from all-forecasts-vs-CLI, walk-forward frozen, binned
by **raw** probability — but do **not** expect calibration to close the market gap.

---

## 1. Files and functions inspected

- `strategy/probability_calibration.py`
  - `calibrate_fair_probability` (inference entry; bins by **raw** prob via `probability_bin`)
  - `_bucket_stats` (training query; reads `signal s ... s.fair_prob AS p_yes`)
  - `shrink_to_observed`, `choose_stats`, `probability_bin`
- `main.py` lines 274–282, 365–369, 438 (calibration applied before storage)
- `config.py` lines 325–337 (calibration params)
- `dashboard/queries.py` `reliability_bins`, `event_reliability_bins`,
  `yes_probability_calibration` (consume the same `s.fair_prob`)

## 2. The defects

**(a) Circular training source.** `main.py` computes `raw_fair_prob =
cdf.prob_between(...)`, then `fair_prob = calibrate_fair_probability(...).calibrated_prob`,
and stores the **calibrated** value into `signal.fair_prob` (the raw is only kept
as text in `signal.notes` as `CAL|raw=`). The calibrator's training query
(`_bucket_stats`) then reads `s.fair_prob AS p_yes` — i.e. it learns the
reliability of its **own post-calibration output**, not of the raw model. The
calibration map is fitted against a moving target it already moved.

**(b) Train/infer bin mismatch.** Inference bins by **raw** prob
(`probability_bin(raw)`); training bins by **calibrated** prob
(`WIDTH_BUCKET(p_yes=fair_prob,...)`). A raw 0.65 that was stored as calibrated
0.55 trains bin 6 but is corrected using... the bin looked up from raw at
inference. The two binnings disagree for a large fraction of signals.

**(c) Signal-log selection.** Training is restricted to buckets/stations/days the
bot actually ticked (rows present in `signal`). The charter requires calibration
"trained on all forecast records versus settlement truth," not the bot's own
signal log.

**(d) Normalization break (cross-reference to the benchmark audit).** The
calibrator adjusts each bucket independently (`shrink_to_observed`, capped at
`MAX_DELTA=0.20`), so the per-event bucket probabilities no longer sum to 1 —
median stored model prob-sum is **1.13**, max **3.25** (see `MARKET_BASELINE_AUDIT.md`
item #8). A coherent forecast distribution must normalize across the ladder.

## 3. Data used and sample size

- `signal` rows in window with a `CAL|` note: **77,616**
- Truth source: CLI only in `_bucket_stats` (correct — matches the settler)
- Config (live defaults): `PROB_CALIBRATION_ENABLED=true`, `DAYS_BACK=60`,
  `MIN_BUCKET_N=20`, `PRIOR_N=15`, `MAX_DELTA=0.20`

## 4. Metrics used

Per-signal raw→calibrated delta; fraction of signals where calibration was applied;
fraction where raw and calibrated fall in **different** decile bins.

## 5. Results

| measure | value |
|---|---|
| signals with calibration note | 77,616 |
| calibration actually moved the prob (raw ≠ cal) | **76,582 (98.7%)** |
| raw and calibrated land in **different** prob bins | **19,262 (24.8%)** |
| mean(calibrated − raw) | **−0.0041** |
| median(calibrated − raw) | **−0.0050** |

**Interpretation.** The calibrator fires on essentially every signal, yet its
**net effect is ~−0.4 pp** — it barely moves probabilities on average. That is
exactly what a circular calibrator does: it measures the residual gap *after*
calibration, finds it ≈ 0, and therefore applies almost nothing. Meanwhile, for
**~25% of signals** the bin used to train differs from the bin used to correct,
injecting inconsistency rather than signal.

This is consistent with the prior independent finding (`calibration_mark_to_settlement.py`,
recorded in project memory) that the **raw** model P(YES) is already well
calibrated (BSS ≈ +0.35). The model's problem is **resolution/center**, not
calibration — and the morning ablation confirms the production stack (with the
calibrator on) is **no better, and on RPS slightly worse**, than a clean NBM-only
rebuild (logged_model RPS 0.1383 vs nbm_only 0.1188; see the GFS report).

## 6. Exact conclusion

The production calibrator is **structurally unsound** — it trains on its own
output, with a 24.8% train/infer bin mismatch, on a signal-log-selected sample,
and it breaks ladder normalization. It should be rebuilt for correctness.
**However, its net effect today is negligible (−0.4 pp), so rebuilding it will
not close the market-relative gap.** Per `WEATHERBOT_PROMOTION_CRITERIA.md` §3,
calibration cannot compensate for a forecast center that loses to the market, and
the evidence here supports that: the binding constraint is upstream (center /
resolution), not the calibration map.

## 7. Statistical limitations

- Raw values were parsed from the `notes` text (`CAL|raw=`); rows without a parseable
  note were excluded.
- The −0.4 pp net is an average over a heterogeneous mix of stations/bins; some
  individual bins may still carry larger (offsetting) corrections.
- "Well-calibrated raw model" rests on the prior mark-to-settlement study, not
  re-derived here; a proper all-forecasts reliability rebuild would confirm it.

## 8. Overfitting risk

**High** for any rebuilt calibrator. A per-station/lead/bin reliability table fit
on ~6 weeks of data will chase noise. Mitigations: bin by raw prob; pool to the
coarsest level that has support; shrink with a prior; **freeze walk-forward**
(train on data strictly before each scored day); validate on the market-relative
benchmark, not on its own training loss.

## 9. Recommended next step

1. Build a **research-only** all-forecasts calibration table: rebuild distributions
   for every settled station-day (not just ticked signals), take the **raw**
   `prob_between` per bucket, score vs CLI truth, bin by raw prob, walk-forward.
2. Compare current vs rebuilt calibrator on Brier, Log Loss, RPS, CRPS **and**
   market-relative skill, out of sample.
3. Separately, fix the **normalization break**: renormalize calibrated bucket
   probabilities across the ladder before they are stored/traded (research flag first).
4. Promote only under `WEATHERBOT_PROMOTION_CRITERIA.md` §3. Expect small gains;
   this is a correctness fix, not the edge.
