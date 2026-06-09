# EXP-2026-014 — Kalshi Market Self-Calibration / Favorite-Longshot Bias (Pre-Registration)

**Date:** 2026-06-09
**Status:** LOCKED 2026-06-09. Design-set characterization + pre-committed forward window.
**Type:** Market-structure study. Research-only, paper-only, no production change.

> Locked fields: snapshot rule (§4), primary rule (§5), costs (§6), decision rule (§7).
> One primary rule. Secondary tables are diagnostics, never decision inputs.

---

## 1. Why this exists (a different question from the closed accuracy axis)

Every closed experiment asked whether WeatherBot's *forecast* beats the market's. This study
asks whether **the market's own prices are internally miscalibrated** — the favorite-longshot
bias documented across betting and prediction markets. If morning favorites are systematically
underpriced, buying them is +EV **with no forecast at all**; the market remains the best
forecaster and still leaks money through price structure. No prior experiment touched this.

**Disclosure (honest peek):** on 2026-06-09 a single exploratory decile query (morning
14–16 UTC midprice vs CLI settlement, TMAX only) was run during the program review and showed
win-rate > price above ~0.6 (n=78) and win-rate < price in the 0.10–0.30 band. This prereg
was written immediately after, with no further looks. The historical run is therefore a
**design-set characterization**, not OOS evidence; the forward window (§7) is the test.

## 2. The single question

At a fixed morning reference time, does realized settlement frequency deviate from the
executable price by more than round-trip costs, in a direction stable across time and
stations?

**Honest prior: weakly positive.** Favorite-longshot bias is the most replicated pricing
anomaly in prediction markets, and retail lottery demand on weather longshots is plausible.
But mid-vs-executable spread and Kalshi taker fees may consume the entire ~5–7pp raw edge.

## 3. Universe

All `kalshi_market` rows, vars `TMAX_DAILY` and `TMIN_DAILY` (reported separately and
pooled), all stations, `valid_date` < run date, with CLI truth in `cli_obs` and a qualifying
morning snapshot. Settlement = CLI vs [lower_f, upper_f), the Kalshi NHIGH rule.

## 4. Reference snapshot (LOCKED)

First `market_snapshot` row per ticker with `ts` in [`valid_date` 14:00 UTC, 16:00 UTC),
`yes_bid` and `yes_ask` non-null, `yes_ask > 0`. Mid = (bid+ask)/2. One snapshot per ticker;
no intraday re-selection. (Same definition as the disclosed peek; kept deliberately so the
design set matches the motivating observation.)

## 5. Primary rule (LOCKED — one rule, no search)

Per event (station, valid_date, var): identify the bucket with the **highest mid**. If that
mid ≥ 0.50, **buy 1 YES contract at the ask**, hold to settlement. No other entries.

Primary metric: **mean net P&L per contract** (settlement payout − ask − fees), with a 95%
**cluster-bootstrap CI clustered by (station, valid_date)** (TMAX and TMIN of the same
station-day share a cluster; 2,000 resamples, percentile interval).

## 6. Costs (LOCKED)

Kalshi taker fee per order: `ceil_to_cent(0.07 × price × (1−price))` per contract, applied at
entry. Settlement has no fee. Diagnostics may additionally report fee-free and mid-fill
numbers, labeled as such; the primary is ask-fill + taker fee.

## 7. Decision rule (pre-committed)

Design set = all history through 2026-06-08.

- **Design pass** requires ALL of: primary net EV/contract > 0 with cluster-bootstrap CI
  excluding 0; positive in both chronological halves of the design set; positive mean in
  ≥ 60% of stations with ≥ 10 events.
- **Design pass → forward window:** freeze this rule verbatim and score it on **fresh
  event-days only** (valid_date ≥ 2026-06-10), evaluating when ≥ 300 fresh events have
  accrued (~2.5 weeks at 20 stations × 2 vars), same metric and CI. Only a forward pass
  earns a paper-trading pre-registration (separate doc, separate approval). Nothing in this
  experiment trades.
- **Design fail:** the market-self-calibration axis closes. The decile tables are reported
  as reference either way.

## 8. Secondary diagnostics (reported, never decision inputs)

Decile calibration table (mid vs win rate, cluster-bootstrap CIs); the symmetric longshot
side (buy NO at no_ask on buckets with mid in [0.10, 0.30)); per-station means; chronological
halves; spread filter (ask−bid ≤ 0.10); TMAX vs TMIN split.

## 9. Validity controls

- Settlement truth from `cli_obs` only (the settlement source), never METAR.
- No survivorship: every ticker with a qualifying snapshot enters; missing-CLI events are
  counted and reported as exclusions.
- Executable side throughout: YES at `yes_ask`, NO at `no_ask`. Mids appear only in bucket
  *selection* (deterministic from the same snapshot) and diagnostics.
- Cluster bootstrap by station-date everywhere (the EXP-C2 reviewer standard).
- The bot's forecast is never an input: this is a market-only study.

## 10. Artifacts

`research/market_longshot_bias.py` (harness, research-only), results to
`EXP_2026_014_RESULTS.md` + registry EXP-2026-014. Evidence run on the VPS.
