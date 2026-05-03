# Weather Bot Backlog

## Next Highest-Leverage Work

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
