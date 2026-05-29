# Independent code & methodology review — 2026-05-29

**Reviewer**: Claude Opus 4.7 (separate session, fresh context)
**Scope**: full repo + DB state + methodology behind today's calibration fixes (commit `4036acb`) and yesterday's CLI/settle work (commits `7254a9b`, `df8558e`, `b71a190`)
**Reading materials consulted**: `main.py`, `strategy/ev.py`, `strategy/early_exits.py`, `strategy/probability_calibration.py`, `models/distribution.py`, `jobs/retrain_bias.py`, `jobs/settle_paper_fills.py`, `data/nws_text_products.py`, recent commits, recent DB state.

---

## TL;DR

Today's overconfidence diagnosis is real and the gates I shipped do fire correctly. But:

1. **I broke a unit test and didn't notice** until I ran the full suite during this review. Fixed in this review's commit.
2. **The widen-factor bump (1.10 → 1.40) has a non-trivial interaction with the existing probability calibrator** that I did not analyze. May suppress the calibrator's correction or stack on top of it; needs validation.
3. **The two new price gates are coarse heuristics** based on a 14-day in-sample backtest. They're shipping with 0 out-of-sample evidence. The underlying calibration shrinkage parameters (`PROB_CALIBRATION_PRIOR_N=35`) are probably the better lever to pull, but I didn't touch them.
4. **The structural take-profit / no-stop-loss asymmetry that's actually causing the daily bleed is not addressed** by today's changes. Block-gates reduce gross exposure to bleeding patterns but don't fix the asymmetric P&L curve.
5. **Yesterday's CLI/settle work holds up** under independent review — the parser fix is clean, the audit query confirms zero discrepancies, the timer ordering works.

---

## What I checked

### 1. Yesterday's fixes (audit_2026_05_28.md scope)

**CLI parser** ([data/nws_text_products.py](data/nws_text_products.py)):
- Anchor-on-MAXIMUM approach is structurally sound. Pseudocode walks every `MAXIMUM` line in the text and attributes it to the nearest preceding section header. False matches on `VALID TODAY` in the body header are impossible by construction.
- 9 unit tests pass against 5 saved fixtures spanning 5 WFOs (KEWX, KOKX, KMFL, KMTR, KBOU). Coverage is reasonable but not exhaustive — fixtures don't cover RTP/DSM products, missing-data cases, or non-English locales (none expected, but documenting).
- The `_select_cli_for_target` change to accept YESTERDAY regardless of `for_date` is justified by the KNYC evidence — confirmed by re-reading the fixture file.

**Settle priority chain** ([jobs/settle_paper_fills.py](jobs/settle_paper_fills.py:43)):
- Kalshi `expiration_value` → CLI → defer is clean. The `NULLIF(... , '')` handling for empty `expiration_value` is correct.
- `_get_obs_value(fill)` signature change correctly threads `kalshi_settle_value` through the SQL join in `list_unsettled_paper_fills`. Verified end-to-end against a recent fill.

**Dashboard timezone**:
- `dashboard/queries.py` 18 `CURRENT_DATE` replacements look correct on inspection. Smoke test against live DB confirms 13 dashboard functions return data.
- One edge case I didn't see called out: between 8pm ET on Saturday and midnight ET, "yesterday's settled fills" filter (`valid_date = ET_today - 1`) refers to **Friday**, while the underlying `paper_fill.ts` is UTC. If a fill was opened at 8pm ET Friday (= 1am UTC Saturday), it appears under Friday in ET-relative reads. This is the desired behavior but worth confirming once more in a few real ET-evening sessions.

**Three-way audit**: `cli_obs` ↔ Kalshi `expiration_value` ↔ paper_fill payouts all return 0 discrepancies across 2026-05-20+. Solid.

### 2. Today's changes (commit `4036acb`)

**The audit that motivated the changes**:
- The calibration table I ran (predicted vs observed by 10-bucket) used 349 fills across 14 days. The overconfidence pattern (10-18pp above 20% bucket) is statistically real — buckets with n≥30 show consistent ~15pp overconfidence.
- **However**: my audit grouped by `model_p_side` (= fair_prob for YES, 1-fair_prob for NO) without separating early-exit fills from held fills. Early-exit captures bias the win-rate calculation: a YES at $0.10 that exits at $0.50 counts as a "win" with model_p=fair_prob, but it didn't necessarily go on to win at settlement. This **understates the held-only overconfidence** and **overstates exit-included win rates**. The directional conclusion holds but the magnitude could be off by several pp.
- The +$339 backtest of "block YES<$0.20 AND NO>=$0.60" is **purely in-sample** on the same 14 days that motivated the gates. No cross-validation, no walk-forward. Standard backtest-overfitting risk applies.

**The gates themselves** ([main.py:299-321](main.py)):
- YES_TAIL_GATE uses `sig.market_ask`. ✓ (the price we'd pay to enter YES)
- NO_CONSENSUS_GATE uses `1 - sig.market_bid`. ✓ (the price we'd pay to enter NO)
- Gate ordering: NO_FADE_GATE → NO_CONSENSUS_GATE → YES_TAIL_GATE. NO_FADE blocks NO < $0.50; NO_CONSENSUS blocks NO ≥ $0.60. **Combined, NO trades survive only in [$0.50, $0.60).** That's a 10-cent window. Concentration risk: any execution slippage, fee load, or market move pushes more NO trades into the blocked zones. Could over-starve NO entries.
- Production verification: 12 YES_TAIL_GATE / 5 NO_CONSENSUS_GATE hits in 15 minutes post-deploy. Gates are firing. **Notably**: the YES_TAIL_GATE blocked signals had `avg_edge_bps=38,292` — the bot's nominal edge calculation thinks these are *huge* opportunities. By blocking them, we're explicitly distrusting the bot's own edge calculation in this regime.

**The widen-factor change** ([models/distribution.py:69](models/distribution.py)):
- Change is 1.10 → 1.40, comment cites empirical justification.
- **Test breakage**: I added the change but didn't update `tests/test_distribution.py:87` which hard-asserted the old value. Caught only by the full-suite run during this review. Fixed in this review's commit (will commit shortly).
- **Interaction with calibrator I missed**: `strategy/probability_calibration.py` already shrinks fair_prob toward observed frequency, looking at decile bins. The decile lookup uses `signal.fair_prob` values that were recorded under the OLD widening. After 1.40 widening, the new raw fair_prob distribution is less peaked. The calibrator will continue using historical decile statistics built from the old distribution. This either:
  - **Stacks** (calibrator shrinks the now-less-peaked raw further toward 0.5) → could overshoot, under-confident
  - **Cancels** (calibrator pulls back toward the OLD overconfident distribution) → no net effect
  - **Wrong-mixes** (calibrator finds different decile bin for the same underlying signal) → unpredictable
  - I did not run a simulation to determine which. Should validate post-deploy.

### 3. Probability calibration module ([strategy/probability_calibration.py](strategy/probability_calibration.py))

The calibrator IS firing on every signal (verified: notes show `CAL|raw=...|cal=...` consistently). It uses an empirical-Bayes-style shrinkage with prior strength `PROB_CALIBRATION_PRIOR_N=35`.

**Sample data from production (last 5 min)**:
```
raw=0.630 → cal=0.550   src=station_lead, n=25, pred=0.633, obs=0.440
```

So calibrator has 25 fills in the station_lead bucket where bot predicted ~63.3% and observed only 44.0%. That's a 19.3pp gap. Shrink = n/(n+35) = 25/60 = 0.42. Applied delta = 0.42 × (0.440 − 0.633) = −0.081. Capped at −0.15. Result: 0.630 + (−0.081) = 0.549 ≈ 0.55.

**Direct observation**: with n=25 and prior_n=35, only 42% of the empirical correction is applied. That leaves ~11pp of residual overconfidence even AFTER calibration. The calibrator's `PROB_CALIBRATION_PRIOR_N=35` is too sticky for the sample sizes we currently have. **Lowering PROB_CALIBRATION_PRIOR_N to ~15 (or making it adaptive) would address the root cause more directly than my widen-factor bump.**

I didn't change this in today's commit. Worth proposing for the 6/9 re-evaluation pass.

### 4. EV / sizing ([strategy/ev.py](strategy/ev.py))

- Kelly fraction with fee-adjustment is mathematically correct.
- Divergence guardrail at `|fair − mid| > 0.50` is so wide it only catches extreme outliers; the 10-18pp overconfidence pattern is well under threshold.
- `MIN_EDGE_BPS=200` (2% of price) is a **fractional** threshold. For YES at $0.04 (just below my new floor), edge needed to pass = $0.0008/contract. That's basically zero. Same threshold for YES at $0.50 needs $0.01/contract. **This price-relative threshold lets tiny-absolute-edge cheap-tail bets through whenever fair_prob disagrees with market by even a percentage point.** Switching MIN_EDGE_BPS to an absolute cents-per-contract threshold (e.g., 2¢) would naturally filter cheap-tail noise without a hard price floor. Worth proposing.

### 5. Early exits ([strategy/early_exits.py](strategy/early_exits.py))

- Three guards (snapshot age, book size, slippage haircut) look correct.
- Take-profit progress formula `(exit_bid - entry_price) / (1 - entry_price)` is the right denominator.
- For YES at $0.20 with TAKE_PROFIT_THRESHOLD=0.70: exit fires when `gain ≥ 0.56` → `exit_bid ≥ 0.76`. That's a 3.8x return.
- For NO at $0.60 with same threshold: max_gain = 0.40, need `(1-yes_bid) − 0.60 ≥ 0.28` → `1 - yes_bid ≥ 0.88` → `yes_bid ≤ 0.12`. So bot exits NO when YES bid drops below 12¢ (from market's prior consensus of ~30¢).
- **No stop-loss**. This is the structural asymmetry causing the daily bleed. The new entry gates partially mask this but don't fix it. **Adding a symmetric stop-loss (exit at `progress ≤ -0.50` for example) would address the root issue.** Worth proposing for after 6/9.

### 6. Settlement ([jobs/settle_paper_fills.py](jobs/settle_paper_fills.py))

- Source priority Kalshi → CLI → defer is correct.
- One race condition I notice: between 14:00 UTC (settled-pull) and 14:23 UTC (settle), Kalshi could release a new settlement that pulled-pull missed. Settle then uses CLI when Kalshi was actually available 22 minutes later. **Mitigation**: have the settled-pull run again at 14:22 UTC. Or: cache Kalshi values for the next 6h in case settle defers. Low priority since CLI fallback is correct.
- The `_yes_wins` boundary semantics: `obs >= lower_f AND obs < upper_f` (lower inclusive, upper exclusive). Matches Kalshi `between` strike type per our parser. ✓

### 7. Bias retrain ([jobs/retrain_bias.py](jobs/retrain_bias.py))

- Uses `daily_obs` (METAR-derived), not `cli_obs`. I initially flagged this as a hypothesis for the calibration error. Empirical check: per-station bias_vs_metar vs bias_vs_cli differ by **≤ 0.4°F** across all 19 active stations on May data. The METAR vs CLI memo (`measured_findings_2026-05-01.md`) describing 0.5-1°F undercount **is overstated for our current data**. Worth correcting the memo, but does NOT explain the 10-18pp calibration error.
- Bias sign convention `mean_bias_f = fcst - obs` matches distribution.py's `cdf.shift -= effective_bias`. Sign is correct.

---

## Issues found and severity

| # | Issue | Severity | Location | Status |
|---|---|---|---|---|
| 1 | Widen-factor bump broke unit test | **HIGH** (caught by review, not by CI) | tests/test_distribution.py:87 | Fixed in this review |
| 2 | Widen-factor interaction with calibrator not analyzed | **MED** — could stack or cancel | models/distribution.py + strategy/probability_calibration.py | Open — needs production observation |
| 3 | NO trades concentrated in [$0.50, $0.60) — small window | **MED** — could over-starve | main.py, both NO gates | Open — watch tomorrow's NO volume |
| 4 | Block-gates trust market over bot's edge calc; coarse heuristic | **MED** — opinion-based, not statistical | main.py | Open — re-evaluate 6/9 |
| 5 | 14-day backtest is purely in-sample | **MED** — overfitting risk | review methodology | Open — observe post-deploy fresh fills |
| 6 | Calibrator PRIOR_N=35 is too sticky for current sample sizes | **MED** — addresses root cause more directly | strategy/probability_calibration.py | Open — propose for 6/9 |
| 7 | MIN_EDGE_BPS is price-relative; lets cheap-tail noise through | **LOW** | strategy/ev.py + config.py | Open — propose absolute-cents alternative |
| 8 | No stop-loss; structural asymmetry causes daily bleed | **LOW** (intentional design) | strategy/early_exits.py | Open — propose symmetric stop-loss |
| 9 | METAR-vs-CLI memo overstates undercount for current data | **LOW** | memory file | Update memo |
| 10 | Settled-pull race vs settle (22-min window) | **LOW** | timer schedule | Open — mitigation is dual-fire |

Issue #1 is the only thing I'd block-ship on. It's already fixed below.

## What I would have done differently

1. **Validate calibrator-distribution interaction before widening the distribution.** I should have written a one-page sim: compare today's signals' fair_prob distribution at widen=1.10 vs widen=1.40, then for each, look up the calibrator's decile bin and apply the calibrator correction, then compare the final calibrated_prob distribution. Without this, I'm flying blind on whether the changes stack productively.

2. **Tune `PROB_CALIBRATION_PRIOR_N` instead of (or in addition to) the widen factor.** The calibrator is the cleaner lever — it's doing exactly the right thing (shrinking toward observed frequency) but too weakly. Dropping PRIOR_N from 35 → 15 would roughly double the strength of the empirical correction. Lower-risk than rebuilding the distribution width.

3. **Run a walk-forward backtest** instead of in-sample. Even a simple "train on weeks 1-2, test on week 3" would have given some confidence.

4. **Drop the YES_TAIL/NO_CONSENSUS gates and instead lower MIN_EDGE_BPS or convert to absolute-cents.** The gates work but are coarse. The real signal is "edge is too small for the price to clear fees + risk." That's expressible without a hard price boundary.

## What stayed the same

- Yesterday's parser fix and audit methodology are independently correct.
- Today's overconfidence diagnosis is empirically valid (the calibration table is the smoking gun).
- The deployed gates DO fire in production and DO block the categories I claimed.

## Recommended next steps (for the 6/9 re-evaluation)

1. **Check whether overconfidence improved by the right amount.** Re-run the calibration table query. Want each bucket's overconfidence < 5pp.
2. **Check whether new gates eat too much volume.** If NO_volume / total_volume drops by >50% vs the 5/15-5/28 baseline, the gates are too tight.
3. **Try `PROB_CALIBRATION_PRIOR_N=15` as an A/B.** Lower risk than touching widen factors. Env-configurable.
4. **Add a stop-loss as `EARLY_EXIT_STOP_LOSS_THRESHOLD=-0.50`** (exit when progress ≤ −50% of max gain). Symmetric to take-profit.
5. **Switch MIN_EDGE_BPS to MIN_EDGE_CENTS.** Existing bps semantics under-filters cheap tails.
