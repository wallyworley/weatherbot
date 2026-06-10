# EXP-2026-015 — Venue-Wide Kalshi Settlement-Calibration Sweep (Pre-Registration)

**Date:** 2026-06-10
**Status:** LOCKED 2026-06-10 (operator-directed: search for structural edge beyond weather).
**Type:** Market-structure study, venue-wide. Research-only, paper-only, no production change.

> Locked fields: reference price (§4), grid (§5), costs (§6), candidate rule (§7). This is a
> SWEEP and therefore a multiple-comparisons machine by construction; the guardrails in §7
> exist precisely because of that. No cell becomes anything without surviving them.

---

## 1. Why this exists

The weather program closed negative on accuracy (C1/C1b/C2, EXP-2026-013) and the weather
favorite-longshot study (EXP-2026-014) found a real bias eaten by costs. The operator has
directed a search for edge elsewhere on Kalshi. The defensible search is the same
**structural** question asked venue-wide: are settled prices calibrated, by category and
price band, at executable prices net of taker fees? This requires no forecast and no
out-speeding anyone. Recon (2026-06-10): ~10,800 series, 15 categories; settled markets
carry result + close_time; daily candlesticks provide per-market yes bid/ask OHLC, so
executable reference prices are reconstructable for the full settled universe.

**Honest prior:** mostly negative. EXP-2026-014's lesson generalizes — visible biases tend
to live inside the spread+fee envelope. The interesting unknowns are the retail corners:
parlays (KXMVE*, the venue's lottery products), Entertainment/Mentions, and long-dated
event markets where "drama premium" decay is a documented retail bias on other venues.

## 2. The single question

At a fixed pre-settlement reference time, does any (category × price-band) cell show
settlement frequency deviating from executable price by more than round-trip costs,
stably across time?

## 3. Universe

All Kalshi markets settled in the trailing **90 days** with a YES/NO `result`, collected via
the authenticated REST API into a research-only table (`kalshi_settled_market`). Candle
reference prices fetched only for markets with `volume_fp >= 500` (illiquid markets are
untradeable and would pollute the sweep); all markets stored regardless for census counts.

## 4. Reference price (LOCKED)

The **daily candlestick for the last full UTC day before the market's close-date** (i.e.
settlement-eve). Reference executable prices: `yes_ask.close_dollars` for buying YES,
`(1 − yes_bid.close_dollars)` for buying NO; mid = (bid+ask)/2 for banding. Markets without
a candle that day (no quotes) are excluded and counted. One reference per market; no
intra-life re-selection. Markets whose lifetime is shorter than one full day are excluded
(no settlement-eve exists).

## 5. Grid (LOCKED)

- **Category**: the series catalog's `category` (15 values) + a separate `PARLAY` cell for
  `KXMVE*` tickers regardless of category.
- **Price band** (on reference mid): [0,.05), [.05,.15), [.15,.35), [.35,.65), [.65,.85),
  [.85,.95), [.95,1].
- Both sides scored per market: buy-YES net EV and buy-NO net EV.
- Cells reported only at n >= 50 markets; smaller cells shown as census only.

## 6. Costs (LOCKED)

Kalshi taker fee `ceil_to_cent(0.07 × price × (1−price))` per contract at the reference
executable price. (Parlay/MVE markets may carry different fee schedules; the report flags
the PARLAY cell as fee-schedule-unverified until checked against the fee docs.)

## 7. Candidate rule (pre-committed; the anti-fool's-gold clause)

Design/holdout split: markets are split **chronologically by close_time into two halves**.
A cell is a **candidate** only if ALL hold in BOTH halves independently:

- net EV per contract > 0 on the same side (YES or NO) with a 95% cluster-bootstrap CI
  (cluster = `event_ticker`) excluding 0;
- n >= 50 markets in each half;
- the implied edge exceeds 1¢ per contract after fees in each half.

Sweep-wide expectation management: with ~16 categories × 7 bands × 2 sides ≈ 224 cells and
two halves, ~3 cells will pass a 5% test per half by chance, but requiring BOTH halves
independently cuts the false-positive expectation to well under one cell. Any candidate
still only earns a **separate forward-window pre-registration** (fresh markets settling
after this study's lock date, n >= 200, same rule verbatim) — never a trading change.
No candidate anywhere: the venue-structure axis closes, like the others.

## 8. Validity controls

- Settlement truth = the API `result` field only.
- No survivorship: every settled market in the window is collected; exclusions
  (no-candle, short-lifetime, sub-volume) are counted and reported.
- Executable side throughout; mid is used only for banding.
- Cluster bootstrap by event_ticker (markets within one event are mechanically dependent —
  bucket ladders, parlay legs).
- Rate-limit citizenship: backfill throttled (<= ~4 req/s), run on the VPS.
- The bot's forecasts are never an input.

## 9. Artifacts

`research/kalshi_settled_calibration.py` (collector + report, research-only),
table `kalshi_settled_market` (migration `db/migrations/2026-06-10_kalshi_settled_market.sql`),
results to `EXP_2026_015_RESULTS.md` + registry EXP-2026-015. Evidence runs on the VPS.

## 10. Scale amendment (LOCKED 2026-06-10, before any candle/report data was seen)

First backfill found the settled universe is ~50x the recon estimate: **~440,000 markets/day**,
~85% zero-volume auto-generated parlay legs (11 days = 4.88M rows / 2.4 GB before pruning).
Amendments, all data-budget only, none results-contingent:

- **Storage floor:** rows stored only at `volume_fp >= 100`; thinner markets are censused by
  count in the collector log (they are untradeable and never reach the candle phase anyway).
  Zero-volume rows already collected are pruned.
- **Stratified candle sample:** per-category cap of **1,500** candle fetches, selected by
  `md5(ticker)` ordering (deterministic, reproducible, unbiased w.r.t. outcome). 1,500 per
  category is ample for the locked 7-band x 2-half grid at n >= 50 per cell. Categories under
  the cap are taken in full.
- Grid, reference price, costs, and the §7 candidate rule are unchanged.
