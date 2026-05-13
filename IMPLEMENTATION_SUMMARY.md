# Weather Bot Calibration Corrective Pass

Updated May 7, 2026.

## Summary

The first May 6 variance fix used a blanket `1.35x` spread inflation for all
`lead_day >= 1` forecasts. That was directionally plausible but too blunt.
The corrected implementation now uses side-adjusted calibration, station-local
lead-day calculations, order-level Kalshi fees, and lead-aware spread widening.

## What Changed

### Lead-aware variance schedule

`models/distribution.py` now uses:

| Lead day | Variance multiplier | Max widening cap |
|---:|---:|---:|
| 0 | 1.00 | 1.10 |
| 1 | 1.25 | 1.35 |
| 2 | 1.15 | 1.25 |
| 3+ | 1.05 | 1.15 |

Rationale:

- Residual-vs-implied spread checks showed roughly L1=1.29, L2=1.22, L3=1.08.
- KMIA L1 did not need blanket `1.35x` widening.
- Same-day uncertainty is already handled by HRRR blending and intraday
  floor/ceiling conditioning.

### Station-local lead days

Lead day now uses station-local date via:

```python
lead_day_for_station(station, target_date, now_utc)
```

This prevents UTC date boundaries from misclassifying KMDW/other stations.

### Order-level Kalshi fees

Kalshi fees are now computed at order level:

```python
fee_for_order(price, contracts)
```

`fee_per_contract(price, contracts)` now returns the effective per-contract fee
for an order. This avoids overstating fees by multiplying a rounded one-contract
fee by the contract count.

### Side-adjusted calibration and P&L

Calibration and expected P&L now use the side actually bought:

```sql
CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END
```

`paper_fill.payout` is treated as side-relative: `payout > 0` means the fill
won, regardless of YES/NO.

### Profitability controls

Future entries now pass through `strategy/profitability.py` after normal model
and safety gates:

- `PAUSED_TRADE_STATIONS=KMDW`
- `KNYC_L1_SIZE_MULT=0.25`
- `NO_UNDER_50C_SIZE_MULT=0.50`
- `YES_25_50C_SIZE_MULT=0.50`

The controls are simple on purpose: pause the worst thin-sample station, reduce
the worst lead-time slice, and downsize weak side/price bands while more data
accumulates.

### Research report for unproven levers

`jobs/profitability_report.py` measures:

- Maker/wait-for-one-cent-better entry replay
- 70% max-gain early-exit replay
- DIVERGENCE skip replay with corrected order-level fees

### Historical backtest repair

`research/backtest_variance_fix.py` now:

- Uses historical `pf.ts` as the as-of timestamp.
- Loads the latest NBM cycle available at that timestamp.
- Uses station-local lead days.
- Recomputes order-level Kalshi fees where historical stored fees may be stale.

## Corrected Baseline

Apr 1-May 6 side-adjusted calibration:

| Lead | Fills | Predicted win | Observed win | Error |
|---|---:|---:|---:|---:|
| L0 | 33 | 0.500 | 0.394 | +0.106 |
| L1 | 96 | 0.700 | 0.542 | +0.159 |

The original `+0.30` to `+0.56` diagnosis was overstated because it used trade
price as a fair-probability proxy and mishandled NO payout semantics.

## Validation Results

Commands run:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q main.py strategy models data dashboard jobs research verification tests
.venv/bin/python research/profile_calibration.py --start-date 2026-04-01 --end-date 2026-05-06
.venv/bin/python research/backtest_variance_fix.py --start 2026-04-01 --end 2026-05-06
.venv/bin/python research/monitor_edge_accuracy.py --hours 1000
git diff --check
```

Results:

- Tests: `23 passed`
- Compile check: clean
- Whitespace check: clean
- Backtest: lead-aware variance improves expected P&L by about `$13.96` on the
  Apr 1-May 6 settled-fill window.
- Corrected L1 calibration baseline: about `+0.159`.

## Files Updated

- `models/distribution.py`
- `strategy/ev.py`
- `strategy/profitability.py`
- `main.py`
- `dashboard/queries.py`
- `dashboard/replay.py`
- `jobs/analyze_edge_breakdown.py`
- `jobs/diagnose.py`
- `jobs/health_check.py`
- `verification/metrics.py`
- `research/profile_calibration.py`
- `research/backtest_variance_fix.py`
- `research/monitor_edge_accuracy.py`
- `research/variance_fix_report.py`
- `jobs/profitability_report.py`
- `tests/test_distribution.py`
- `tests/test_ev.py`
- `tests/test_profitability.py`
- `NEXT_STEPS.md`

## Operating Guidance

- Do not return to blanket all-lead widening unless a fresh replay proves it.
- Tune by lead day first, then by station only after enough sample size.
- Keep maker execution, early exits, and DIVERGENCE auto-trading research-only
  until their reports show durable positive dollar impact.
- Keep `PAPER_MODE=true` until side-adjusted calibration, expected-vs-realized
  edge, and net P&L are stable for multiple weeks.
- Use `NEXT_STEPS.md` as the current runbook.
