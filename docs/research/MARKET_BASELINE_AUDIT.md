# Market-Relative Benchmark Audit

**Date:** 2026-06-06
**Auditor role:** Lead quantitative researcher (benchmark validity review)
**Subject:** `docs/research/MARKET_RELATIVE_CENTER_BENCHMARK_2026_06_07.md` produced by
`research/market_relative_center_benchmark.py`
**Thesis under test:** "WeatherBot does not beat the market-implied forecast; the
binding problem is forecast information/resolution, not trading strategy."

---

## Verdict

**BENCHMARK CONFIRMED (direction), with one methodological correction that does
not change the conclusion.**

The headline result reproduces exactly, the pairing/settlement/normalization/
metric implementations are correct, and the single genuine defect found
(timestamp incoherence in bucket selection) was independently corrected and the
conclusion held under the corrected method. The market-implied forecast beats
WeatherBot on Brier, RPS, CRPS and center MAE across every scored station/lead
group.

The one caveat: the production report **overstates the CRPS and center-MAE
magnitude** by roughly 40% / 20% because it pools buckets sampled across a
median 9.2-hour window at lead 0. The Brier and RPS gaps are, if anything,
slightly understated. Direction and significance are unaffected.

---

## 1. Files and functions inspected

- `research/market_relative_center_benchmark.py`
  - `collect_bucket_rows` (the selection SQL, `ROW_NUMBER() ... PARTITION BY ticker, lead_day ORDER BY ts DESC`)
  - `_REALIZED_SQL`, `_YES_WIN_SQL` (settlement mapping)
  - `score_event`, `score_events` (pairing, winner detection)
  - `_normalize`, `_clamp_prob` (bucket normalization)
  - `_brier`, `_rps`, `_crps_discrete`, `_expected_value`, `_bucket_representative`, `_typical_bucket_width` (metrics)
  - `summarize_group`, `_paired_ci`, `_evidence_statement` (aggregation, CIs)
- `data/persistence.py` — `connect`, table access (no model code imported by the benchmark)
- Database tables: `signal`, `kalshi_market`, `cli_obs`, `daily_obs`, `stations`, `metar_obs`

Research-only diagnostics built for this audit (no trading logic touched):
- `research/snapshot_market_benchmark.py` — coherent-snapshot re-scoring (reuses the
  production scoring functions unchanged; only the row selection differs)

---

## 2. Data used and sample size

- Window: `--days 3650 --max-lead-day 7 --var TMAX_DAILY` (the report's parameters)
- **561 scored events** total: 291 at lead 0, 270 at lead 1. (Leads ≥2 produced
  no events meeting the ≥3-bucket / single-winner criteria — the bot's stored
  signals concentrate at lead 0–1.)
- Per-station samples are small (n=10–11) for 16 stations; only KMDW (35/34),
  KMIA (35/34), and KNYC (45/42) have larger samples. **The aggregate (n=561) is
  robust; per-station/lead confidence intervals are wide.**
- Truth source coverage across all 367,689 bucket-signals in window:
  - Kalshi `expiration_value` present: 125,275
  - CLI fallback used: 220,074
  - `daily_obs` fallback used: **0** (the corruption-prone path is never exercised)
  - No truth (excluded): 22,340

## 3. Metrics used

Mean per-bucket Brier; normalized ranked probability score (RPS, divisor n−1);
discrete energy-form CRPS in °F over bucket-center representatives; center MAE
(|E[X]−truth|). Paired event-level normal-approximation 95% CIs.

---

## 4. Audit checklist results

| # | Audit question | Finding | Status |
|---|----------------|---------|--------|
| 1 | Reproduce headline | Re-ran the module: weighted Brier=+0.0645, RPS=+0.0668, CRPS=+0.454 F; 38 market-better groups, 0 model-better. Identical to the committed report. | ✅ Pass |
| 2 | Bucket normalization over identical set | Both `model_p` and `market_p` are `_normalize`'d over the **same** captured `rows`. Correct. | ✅ Pass |
| 3 | Station/date/lead pairing | Events grouped by `(station, valid_date, var, lead_day)`; all metrics computed on identical rows; CIs are paired event-level deltas. | ✅ Pass |
| 4 | Market midpoint construction | `(market_ask + market_bid)/2` from the **same signal row** as `model_p` (no cross-time mixing of model vs market within a bucket). Fee-free. Median over-round ~6.5%, normalized away. | ✅ Pass |
| 5 | Settlement mapping | `COALESCE(expiration_value, CLI, daily_obs)`; `[lower_f, upper_f)` winner rule. Where both exist, `expiration_value == CLI` on **582/582** station-days (0 disagreements, mean diff 0.0). **561/561** events have **exactly one** winner → ladders are mutually exclusive, the winning bucket is always captured, and the `≠1 winner` filter drops **zero** events (no selection bias). | ✅ Pass |
| 6 | Timestamp leakage | `model_p` and `market_p` come from one stored `signal` row at a single `ts`; truth is ex-post settlement (legitimate). No future market prices enter the scored object. | ✅ Pass |
| 7 | Brier/RPS/CRPS correctness | Brier = mean per-bucket SE; RPS normalized, computed on buckets **sorted by `lower_f`** (order-correct); CRPS = E\|X−y\| − ½E\|X−X'\| (correct energy form); center = Σx·p. All applied symmetrically to model and market. | ✅ Pass |
| 8 | Probabilities form valid distributions | **FAIL (model side).** Raw model bucket probs do **not** sum to 1: median 1.13, mean 1.18, **max 3.25**; 49% of events deviate >0.15. (Market sums median 1.07 — normal vig.) The benchmark normalizes this away, which is *generous* to the model. See §5. | ⚠️ Documented |
| 9 | Timestamp coherence of the distribution | **FAIL.** "Latest signal per bucket" samples each bucket at its own last-quote time. Lead-0 events span a **median 9.2 h** (98% > 2 h, max 19.5 h) across buckets; lead-1 median 3.1 h. The per-event "distribution" is one the model never held at any instant. See §5. | ⚠️ Defect |
| 10 | CRPS bucket-center approximation | Open-ended buckets use edge ± ½·median-width; symmetric for model and market. Fine for relative comparison; absolute CRPS slightly understates tail mass. | ✅ Acceptable |

---

## 5. The one material defect: timestamp incoherence — and why the conclusion survives

**Mechanism.** `collect_bucket_rows` keeps, per bucket ticker, the latest signal
that still had a two-sided market quote. Dead buckets lose their quotes earlier
in the day, so their "latest" signal is hours older than the live buckets'. The
event's buckets are then pooled and normalized into a single distribution. Worked
example (KNYC 2026-04-26, lead 0): the `<51` bucket's last quote was 14:03, the
`57–59` bucket's was 23:50 — a 9.8 h spread. Raw model probs summed to 3.25.

**Robustness test.** `research/snapshot_market_benchmark.py` re-selects, per
event, the latest 10-minute window in which ≥3 buckets were simultaneously live
(the bot emits all buckets in synchronized ~15-min ticks, so coherent snapshots
exist), then scores with the **identical** production scoring functions. Result
(median intra-snapshot spread collapses to 0.01 h, 561/561 events retained):

| metric (weighted) | production (latest-per-bucket) | coherent snapshot | effect |
|---|---|---|---|
| Brier delta | +0.0645 | **+0.0684** | slightly wider |
| RPS delta | +0.0668 | **+0.0688** | slightly wider |
| CRPS delta (°F) | +0.454 | **+0.286** | ~40% smaller |
| market-better groups | 38 / 38 | 34 / 34 | unchanged direction |

Lead-0 detail under the coherent snapshot: model Brier 0.190 vs market 0.087
(+0.103); model RPS 0.159 vs market 0.066 (+0.093); model CRPS 0.900 vs 0.582
(+0.318); center MAE 1.23 vs 0.75 (+0.48).

**Conclusion of the test:** the time-incoherent pooling did **not** manufacture
the model's loss. Under a coherent point-in-time snapshot the market still beats
WeatherBot on every metric and at every lead; the Brier/RPS gaps widen slightly
and only the CRPS/center *magnitude* shrinks. The directional thesis is robust to
the defect.

**On item #8 (probabilities not summing to 1):** this is a real *production*
property — the per-bucket calibrator (see `CALIBRATOR_REBUILD_REPORT.md`) adjusts
each bucket independently and destroys normalization. The benchmark repairs it
before scoring, so the benchmark is conservative: even after the model's
distribution is renormalized for it, it still loses.

---

## 6. Statistical limitations

- Per-station/lead samples (n≈10–11 for most stations) are too small to rank
  individual stations; only the aggregate and the three high-n stations
  (KMDW/KMIA/KNYC) carry weight individually.
- Only leads 0–1 are populated; the benchmark says nothing about leads ≥2.
- Normal-approximation paired CIs on per-event deltas; with right-skewed score
  deltas these are approximate at small n (bootstrap would be tighter but the
  aggregate sign is not in doubt).
- The comparison is in-sample to the *bot's own historical operation* (it scores
  signals that were actually emitted). It is a faithful description of past
  performance, not an out-of-sample forecast experiment.

## 7. Overfitting risk

**Low.** This is an audit/description of already-logged data with no parameters
fit. The only modelling choice introduced (coherent-snapshot selection) has no
free parameters beyond the tick window (10 min) and min-bucket count (3); the
result is stable to those.

## 8. Recommended next steps

1. **Adopt coherent-snapshot selection as the canonical benchmark method.**
   Promote `snapshot_market_benchmark.py`'s selection into the production report
   (research-only), or add it alongside, and restate the report's CRPS/center
   numbers using it. The latest-per-bucket selection should be retired because it
   reports an incoherent distribution.
2. **Add the frozen regression fixture** (Priority Backlog Story 1.2): a small
   settled-signals fixture + a test that reproduces the scored metrics, so future
   scoring changes are caught.
3. Treat the benchmark as the **standing market baseline**: every forecast change
   must be scored against it out of sample (see `WEATHERBOT_PROMOTION_CRITERIA.md`).
4. The "probabilities don't sum to 1" finding (item #8) is itself a defect — see
   `CALIBRATOR_REBUILD_REPORT.md`.

---

## 9. Exact conclusion

The market-relative benchmark is **methodologically valid for its directional
claim** and the claim **reproduces**: WeatherBot's stored forecast distribution
is beaten by the Kalshi market-implied distribution on Brier, RPS, CRPS and
center MAE at both populated lead days, across all 38 (corrected: 34)
station/lead groups, with zero groups favoring WeatherBot. The only correction
needed — coherent-snapshot bucket selection — was applied and the conclusion
held. **WeatherBot does not currently beat the market-implied forecast.**
