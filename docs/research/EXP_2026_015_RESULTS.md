# EXP-2026-015 — Venue-Wide Settlement-Calibration Sweep — Results

_run 2026-06-10 07:32 UTC on the VPS (`research/reports/exp_2026_015_results.md`, canonical
machine artifact). Locked prereg: `EXP_2026_015_VENUE_CALIBRATION_SWEEP.md` (+ §10 scale
amendment)._

## VERDICT: ZERO CANDIDATES — the venue-structure axis closes

12,915 settled markets scored (stratified sample of a 7.0M-market / 90-day census across all
15 categories + PARLAY), settlement-eve executable candle prices, taker fees in, cluster
bootstrap by event, chronological halves. **No (category × band × side) cell passes the
locked dual-half candidate rule. Most cells are negative on BOTH sides** — the signature of
prices sitting inside the spread+fee envelope, i.e. a venue that is well calibrated at
executable prices essentially everywhere it is liquid enough to measure.

## Census (90 days)

| | markets |
|---|---:|
| settled universe scanned | ~15.9M (8.9M census-skipped at volume < 100) |
| stored (volume ≥ 100) | 7,048,516 |
| eligible (volume ≥ 500, life ≥ 1 day) | 432,103 + 12,915 sampled |
| scored (sampled, candle OK) | 12,915 |
| no settlement-eve quote | 3,720 |

## Findings worth keeping (descriptive, none decision-grade)

1. **Parlays cannot even be tested at top-of-book, let alone beaten.** Every sampled parlay
   (n=1,031) lands in the 0.35–0.65 mid band because settlement-eve books are quoted
   absurdly wide (bid ≈ 0, ask ≈ 1 ⇒ mid ≈ 0.5). Buy-YES EV ≈ **−0.80**/contract, buy-NO
   ≈ −0.19: both sides lose enormously. The retail-lottery hypothesis dies not because
   parlays are fairly priced but because the maker quotes leave nothing harvestable at
   executable prices. (Win rate at "mid 0.50" is 16–21%, confirming the asks are far above
   fair value — and unhittable profitably from either side.)
2. **The favorite-longshot lean replicates venue-wide but never clears costs.** The
   0.65–0.85 favorite band leans buy-YES-positive in both halves in Politics (+6.8¢/+6.0¢),
   Economics (+0.3¢/+2.3¢), and Sports (+0.4¢/+1.9¢), while longshot bands lean negative —
   the same direction EXP-2026-014 found in weather. None passes: Politics fails the n ≥ 50
   per-half gate (47/27), Sports and Economics fail the 1¢ edge floor. This is the third
   independent measurement of the same bias being eaten by spread + taker fees.
3. **Entertainment mid-band is the worst place on the venue to buy YES** (EV −0.37/−0.47
   per contract): wide spreads on subjective markets, not an edge for the other side either.

## Decision (per prereg §7)

No candidate anywhere → **the venue-structure axis closes**, joining accuracy (C1/C1b/C2,
EXP-2026-013), weather price-structure (EXP-2026-014), and pending only the latency axis
(EXP-2026-011, evidence ≥ 2026-06-23). No forward window opens; no production change;
nothing traded. Re-opening requires a new prereg on genuinely new structure (e.g. a future
fee-schedule change or a new product class quoted with tight books).

Operational note: the 90-day census in `kalshi_settled_market` (7.0M rows) is retained per
the operator's keep-everything decision; the collector is one command to re-run if a
standing (e.g. monthly) sweep is ever wanted.
