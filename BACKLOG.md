# Weather Bot — Backlog

## Calibration: bias-table refinements (queued after task #2)

The single-dimension `(month, lead_day=1)` bias table is climatological-mean only. It will systematically misfire when a specific cycle's error diverges from the seasonal mean — exactly what happened on Apr 20, 2026 (table said NBM April runs +4.34°F warm; that day's stale 00z was running ~5°F cold; correction pushed forecasts further wrong-direction).

Three structural fixes, in priority order:

1. **Cycle-hour stratification.** Add `cycle_hour` (0/6/12/18 UTC) to `station_bias` PK. Different cycles have different error profiles — overnight cycles lack same-day data assimilation; afternoon cycles partially incorporate observed peak temps. Combining them into one mean blurs both signals.

2. **Staleness-aware deweight.** When the freshest available cycle is >8h old, attenuate bias correction proportionally. Stale cycles' systematic error is dominated by initialization drift, not the climatological pattern the bias table captures.

3. **Bypass bias on guardrail trip.** When the divergence guardrail fires (`|fair - mkt_mid| > 0.50`), the bias-corrected fair value is the part being doubted. Recomputing fair without bias correction (and re-checking divergence) tells us whether the bias table itself is the source of the disagreement vs. genuine model-vs-market signal.

## Ingestion: preserve cycle history (blocks meaningful lead_day≥2 backtests)

`prob_forecast` currently upserts on `(station, model, var, valid_date, percentile)`, keeping only the latest cycle per target date. This collapses the lead_day dimension — we can only ever populate `lead_day=1` rows from the prior-day morning cycle that survived overwrite. No way to compute lead-0 (same-day) or lead-2+ (multi-day-ahead) bias rows from current data.

Fix: change PK to `(station, model, run_time, valid_date, var, percentile)` and stop overwriting. Then re-run retrain with full lead_day granularity. New cycle history starts accumulating from change date — give it 30 days before re-evaluating.

## Replay harness (continuation-prompt task #3)

Point-in-time backtest framework. Depends on cycle-history retention above.

## Parameter sweep (continuation-prompt task #4)

HRRR weight curve, staleness deweight, widening cap, tail scale, shrinkage prior_n.
Depends on replay harness.

## market_snapshot: extend to NO-side capture

Current table captures only YES-side top-of-book (yes_ask, yes_bid, yes_ask_size, yes_bid_size). For backtest fidelity we also need NO-side, since the bot routinely trades NO when YES liquidity is thin.

Add columns:
```sql
ALTER TABLE market_snapshot
    ADD COLUMN no_ask NUMERIC,
    ADD COLUMN no_bid NUMERIC,
    ADD COLUMN no_ask_size INT,
    ADD COLUMN no_bid_size INT,
    ADD COLUMN status TEXT;
```

Extend `_snapshot_row` in `pull_kalshi_markets.py` to extract from `payload.orderbook_fp.no_dollars` using the same `_best_price` helper pattern from `main.py`. Also capture `payload.status` so backtests can distinguish active from settling/closed quotes.

Not blocking — current YES-only snapshots still yield a partial but useful dataset.
