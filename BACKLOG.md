# Weather Bot Backlog

## Next Highest-Leverage Work

### HFMETAR rollout — DONE phases 1+2+3 (2026-05-03)

Backfill, live path, and bias retrain all complete. KMDW + KMIA now use 5-min
MADIS HFMETAR via IEM for both `metar_fetcher.backfill()` and live `run()`.
KNYC stays on aviationweather.gov hourly METAR (non-ASOS coop site, no feed).
`Station.is_asos` flag drives routing. KMIA bias rows shifted ~−0.81°F per
bucket. Verification numbers post-rebackfill: KMDW |bias|=0.06°F, KMIA |bias|=0.05°F
vs CLI (was +0.73 / +0.81 pre-rollout).

**Open: phase 4 review on/after 2026-05-10.** Run
`python -m research.review_hfmetar_impact` to compare pre/post forecast
accuracy and paper-fill PnL. If KMIA post-cutover MAE and PnL/contract are
no worse than pre, consider:
- Loosening the CLI-required gate in `settle_paper_fills`: with daily_obs
  now within ~0.05°F of CLI for ASOS stations, the bot could reasonably
  settle on daily_obs when CLI is delayed past the 11AM ET cutoff (currently
  the bot waits or skips).
- Promoting a graduated KMDW once it accumulates bias-table samples.

### Point-in-time replay and verification

The dashboard has a useful counterfactual replay tool, but the nightly
`verification` table is still a simplified calibration smoke test. Before live
trading, build a replay harness that evaluates every historical signal using
only data available at the signal timestamp:

- NBM percentiles with `run_time <= signal.ts`
- HRRR/GFS deterministic forecasts with `run_time <= signal.ts`
- bias rows or archived bias snapshots available at that timestamp
- market snapshot top-of-book available at that timestamp
- CLI truth when settled

This should produce the graduation metrics: Brier, log loss, realized vs
expected PnL, reliability bins, and station/model slices.

### Cycle-hour bias stratification

Current `station_bias` keys by `(station, model, var, month, lead_day)`.
Add `cycle_hour` for NBM cycles `(0, 6, 12, 18 UTC)`. Different cycles can have
different error profiles because same-day data assimilation changes through
the day. This is the remaining bias-table refinement after staleness deweight
and divergence bypass shipped.

### Parameter sweep

Automate sweeps over:

- HRRR weight curve
- staleness deweight thresholds
- widening cap
- tail scale
- shrinkage prior_n
- divergence threshold

Run sweeps through the point-in-time replay harness, not through latest-data
verification.

### Snapshot completeness audit

NO-side snapshot fields and market status are now captured. Add a health check
that reports how often `market_snapshot` rows have missing YES or NO quotes, by
series and station. This tells us whether backtests are using complete books or
only partial top-of-book data.

## Completed Cleanup

- `prob_forecast` preserves cycle history with primary key
  `(station, model, run_time, valid_date, var, percentile)`.
- `market_snapshot` captures NO-side top-of-book fields.
- `market_snapshot.status` is captured for active vs stale-book filtering.
- `nightly_verify` is verification-only. Bias writes are owned by
  `jobs.retrain_bias`.
- Chicago markets are mapped to KMDW (Midway), not KORD (O'Hare).
