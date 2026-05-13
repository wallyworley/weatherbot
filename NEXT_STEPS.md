# Weather Bot Calibration Runbook — Current Instructions

Updated after the May 7 corrective pass. The old blanket `1.35x` variance
inflation is no longer the recommended methodology.

## Current Methodology

### 1. Use side-adjusted fair probability

Calibration checks must compare the probability of the side actually bought
against whether that side won:

```sql
CASE
  WHEN pf.side = 'YES' THEN s.fair_prob
  ELSE 1.0 - s.fair_prob
END AS p_side
```

Do not use trade `price` as a proxy for fair probability. It overstated the
lead-1 calibration problem.

### 2. Treat paper_fill payout as side-relative

`paper_fill.payout > 0` means the fill won, regardless of whether the fill was
YES or NO. Do not invert `payout` for NO fills during calibration.

### 3. Compute lead_day in station-local time

Use `lead_day_for_station(station, valid_date, now_utc)` from
`models/distribution.py`. Do not use bare UTC dates or `ts.date()` for Chicago
or other non-UTC station-local markets.

### 4. Use lead-aware variance inflation

`models/distribution.py` now applies:

```python
if lead_day == 1:
    target_std *= 1.25
elif lead_day == 2:
    target_std *= 1.15
elif lead_day >= 3:
    target_std *= 1.05
```

Same-day (`lead_day == 0`) is unchanged because HRRR blending and intraday
floor/ceiling conditioning dominate same-day uncertainty.

Widening caps are also lead-aware:

```python
L0: 1.10
L1: 1.35
L2: 1.25
L3+: 1.15
```

### 5. Use order-level Kalshi fees

Kalshi fees round at the order level:

```python
fee_for_order(price, contracts)
```

Do not multiply the one-contract fee by contract count. That overstates fees
on larger orders and distorts `FEE_LOAD`, EV, Kelly sizing, and P&L.

### 6. Use profitability controls while samples are thin

Default controls:

```dotenv
PROFIT_CONTROLS_ENABLED=true
PAUSED_TRADE_STATIONS=KMDW
KNYC_L1_SIZE_MULT=0.25
NO_UNDER_50C_SIZE_MULT=0.50
YES_25_50C_SIZE_MULT=0.50
```

These are intentionally simple:

- Pause KMDW until it earns its way back with more settled fills.
- Quarter-size KNYC day-ahead entries.
- Half-size historically weak side/price bands.

### 7. Use event-weighted signal calibration before sizing

The empirical probability calibrator now trains from logged signals with known
CLI/daily outcomes, not just settled paper fills. Repeated scores for the same
ticker/probability bucket are weighted to one effective event so one station-day
cannot dominate the reliability diagram or live adjustment.

Fallback order:

```text
station + lead_day + probability bucket
lead_day + probability bucket
station + probability bucket
global probability bucket
```

Defaults are intentionally conservative while sample sizes are small:

```dotenv
PROB_CALIBRATION_MIN_BUCKET_N=20
PROB_CALIBRATION_PRIOR_N=35
PROB_CALIBRATION_MAX_DELTA=0.15
```

## Current Baselines

Corrected Apr 1-May 6 side-adjusted calibration:

| Lead | Fills | Predicted win | Observed win | Error |
|---|---:|---:|---:|---:|
| L0 | 33 | 0.500 | 0.394 | +0.106 |
| L1 | 96 | 0.700 | 0.542 | +0.159 |

By station for L1:

| Station | Fills | Error |
|---|---:|---:|
| KNYC | 73 | +0.164 |
| KMIA | 18 | +0.098 |
| KMDW | 5 | +0.308 |

Interpretation: L1 is still overconfident, but the issue is much smaller than
the old price-proxy diagnosis suggested.

## Validation Commands

Run after changes:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q main.py strategy models data dashboard jobs research verification tests
.venv/bin/python research/profile_calibration.py --start-date 2026-04-01 --end-date 2026-05-06
.venv/bin/python research/backtest_variance_fix.py --start 2026-04-01 --end 2026-05-06
.venv/bin/python research/monitor_edge_accuracy.py --hours 1000
.venv/bin/python -m weather_bot.jobs.profitability_report --days-back 30
.venv/bin/python -m weather_bot.jobs.forecast_benchmark_report --days-back 30
.venv/bin/python -m weather_bot.jobs.shadow_ensemble_report --days-back 30
```

Expected current smoke results:

- Tests: `23 passed`
- Backtest: lead-aware expected P&L improves by about `$14` on Apr 1-May 6
- L1 calibration baseline: about `+0.159`
- Profitability report writes `research/reports/profitability_report_<date>.md`

## Monitoring Instructions

Daily:

```bash
.venv/bin/python research/monitor_edge_accuracy.py --hours 24
```

Weekly or after enough new settled fills:

```bash
.venv/bin/python research/profile_calibration.py --start-date 2026-05-07 --end-date 2026-05-14
.venv/bin/python research/backtest_variance_fix.py --start 2026-05-07 --end 2026-05-14
```

Use at least 30-50 settled fills before changing multipliers again. KMDW and
KMIA are still thin enough that station-specific conclusions can wobble.

## Standing Research Goal

Keep improving the bot's information advantage by continuously researching new
forecast sources, settlement mechanics, and Kalshi price-formation behavior.

Priority questions:

1. **WeatherNext 2 access and value** — when Google grants access, set
   `WEATHERNEXT_BQ_TABLE`, run `jobs.forecast_benchmark_report` and
   `jobs.shadow_ensemble_report`, and compare WeatherNext p50/p10/p90 against
   NBM/GFS/ECMWF before changing production weights.
2. **Kalshi price formation** — treat Kalshi prices as central-limit-order-book
   prices, not bookmaker odds. Research whether active weather-market makers
   appear to anchor to NWS/NBM/GFS/ECMWF, commercial forecast APIs, live ASOS
   observations, or simple settlement-source arbitrage.
3. **Weather settlement mechanics** — keep verifying each city/station's source,
   NWS CLI timing, local standard time window, 6h/24h high consistency checks,
   and revision/delay rules. These mechanics can create edge independent of
   raw forecast accuracy.
4. **Market microstructure** — watch spread, depth, snapshot age, orderbook
   imbalance, and late-day two-bracket markets. Compare model edge to what can
   actually be filled after fees and missed-fill risk.

Current working assumption from source review:

- Kalshi weather contracts settle from the NWS Daily Climate Report / CLI, not
  weather apps or generic forecast feeds.
- Kalshi prices are produced by trader and market-maker orders in a central
  order book; a YES price roughly maps to market-implied probability, but thin
  depth, fees, spreads, and market-maker anchoring can distort that mapping.
- For weather markets, useful edge is likely a combination of better CLI
  settlement modeling, faster observation awareness, and knowing when the
  orderbook is mechanically stale.

## Tuning Rules

If L1 remains overconfident above `+0.15` after a fresh sample:

- Consider L1 `1.30x`, not a blanket all-lead change.
- Keep L2/L3 separate.
- Re-run the backtest and side-adjusted profiler before deploying.

If L1 becomes underconfident below `-0.05`:

- Reduce L1 to `1.20x`.
- Check signal volume and skipped low-edge trades.

If only one station drifts:

- Prefer station-specific calibration or bias-table investigation over global
  multiplier changes.

## Files That Encode The Methodology

- `models/distribution.py` — lead-day helper, variance multipliers, widening caps
- `strategy/ev.py` — order-level fee formula and effective per-contract fee
- `main.py` — station-local bias gate lead-day and order-level paper-fill fees
- `research/profile_calibration.py` — side-adjusted calibration profiler
- `research/backtest_variance_fix.py` — historical as-of replay for variance changes
- `research/monitor_edge_accuracy.py` — live/post-change calibration monitor
- `dashboard/queries.py` and `jobs/health_check.py` — side-adjusted expected P&L
- `strategy/profitability.py` — station/lead/price-band sizing controls
- `jobs/profitability_report.py` — maker/wait, early-exit, divergence research
- `jobs/forecast_benchmark_report.py` — stored NBM/HRRR/GFS/ECMWF benchmark vs CLI truth
- `jobs/shadow_ensemble_report.py` — shadow-only blended model replay

## Important Non-Changes

- `PAPER_MODE` remains the right default.
- Agreement gate remains diagnostic only unless a dollar-impact backtest proves it helps.
- Divergence guardrail is still active at `0.50`; review separately after the
  corrected calibration has a larger sample.
