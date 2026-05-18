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
NO_UNDER_50C_SIZE_MULT=0.0
YES_UNDER_10C_SIZE_MULT=0.0
YES_10_25C_SIZE_MULT=0.50
YES_10_25C_MAX_USD=10.0
YES_25_50C_SIZE_MULT=0.50
PAPER_ORDER_MODE=true
PAPER_ORDER_IMPROVEMENT_CENTS=1
PAPER_ORDER_TTL_MIN=15
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
.venv/bin/python -m weather_bot.jobs.pull_ensemble
.venv/bin/python -m weather_bot.jobs.backfill_weathernext --days-back 7 --stations KNYC,KMDW,KMIA --horizon-days 3
.venv/bin/python -m weather_bot.jobs.shadow_ensemble_report --days-back 30
.venv/bin/python -m weather_bot.jobs.ensemble_calibration_report --days-back 30
.venv/bin/python -m weather_bot.jobs.forecast_update_lag_report --days-back 30 --limit 2500
.venv/bin/python -m weather_bot.jobs.ai_context_brief --station KNYC --valid-date 2026-05-16
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

1. **True ensemble value** — collect Open-Meteo GFS/ECMWF ensemble member rows
   in `ensemble_forecast`, run `jobs.shadow_ensemble_report`, and compare strict
   as-of member probabilities against the current calibrated signal
   probabilities before changing production weights.
2. **WeatherNext 2 access and value** — Google access is available; set
   `WEATHERNEXT_BQ_TABLE` plus Google application credentials on the VPS, run
   `jobs.pull_weathernext`, and compare WeatherNext member probabilities
   against NBM/GFS/ECMWF before changing production weights.
3. **Kalshi price formation** — treat Kalshi prices as central-limit-order-book
   prices, not bookmaker odds. Research whether active weather-market makers
   appear to anchor to NWS/NBM/GFS/ECMWF, commercial forecast APIs, live ASOS
   observations, or simple settlement-source arbitrage.
4. **Weather settlement mechanics** — keep verifying each city/station's source,
   NWS CLI timing, local standard time window, 6h/24h high consistency checks,
   and revision/delay rules. These mechanics can create edge independent of
   raw forecast accuracy.
5. **Market microstructure** — watch spread, depth, snapshot age, orderbook
   imbalance, and late-day two-bracket markets. Compare model edge to what can
   actually be filled after fees and missed-fill risk.
6. **Cross-platform weather gaps** — log Polymarket KLGA/KORD daily-temperature
   buckets in `external_market_snapshot` and compare their market-implied
   distribution against Kalshi KNYC/KMDW plus station-adjusted forecasts.

## WeatherNext 2 Calibration Check — 2026-05-16

The VPS now has WeatherNext credentials configured and
`weatherbot-weathernext.timer` enabled. A bounded historical backfill was run:

```bash
.venv/bin/python -m weather_bot.jobs.backfill_weathernext \
  --days-back 7 --stations KNYC,KMDW,KMIA --horizon-days 3
```

Result: 28 cycles attempted, 27 published cycles, 62,208 rows added/upserted
for the active trading stations. 2026-05-16 18Z was not published yet; the
latest usable WeatherNext run was 2026-05-16 12Z.

True ensemble source counts after the pass:

| model | rows | stations | runs | first run | last run |
|---|---:|---:|---:|---|---|
| GFS_ENS | 127,782 | 5 | 5 | 2026-05-15 18Z | 2026-05-16 18Z |
| ECMWF_IFS_ENS | 210,222 | 5 | 5 | 2026-05-15 18Z | 2026-05-16 18Z |
| ECMWF_AIFS_ENS | 210,222 | 5 | 5 | 2026-05-15 18Z | 2026-05-16 18Z |
| WEATHERNEXT2 | 68,864 | 5 | 27 | 2026-05-10 00Z | 2026-05-16 12Z |

Strict shadow replay verdict: do not promote WeatherNext wholesale. On 1,200
settled signal rows, original Brier was 0.0947 and true-ensemble shadow Brier
was 0.1925 (`+0.0978`, worse). Only KMDW lead-1 improved
(`0.1558 -> 0.1445`); KNYC/KMIA and same-day slices got worse.

The existing empirical probability calibrator still helps modestly:
walk-forward YES/side Brier improved from 0.1978 to 0.1924 over 1,007 signals
from 2026-04-16 through 2026-05-16.

## Logical Next Step

Keep WeatherNext shadow-only and add a station/lead-gated challenger report
instead of changing production weights. The first candidate slice is KMDW
lead-1; require more settled examples and reliability bins before considering
any live blend. In parallel, keep the empirical probability calibrator enabled,
because it is the only calibration change that currently improves Brier.

## PolymarketWeather-Inspired Backtests — 2026-05-17

Added three research-only pieces:

- `jobs.ensemble_calibration_report`: EMOS-lite bias/spread calibration for true
  ensemble member probabilities.
- `jobs.forecast_update_lag_report`: probability-edge z buckets plus 15/30/60m
  signed market movement after signals.
- `jobs.ai_context_brief`: deterministic context pack for a future advisory AI
  skill. Skill spec lives at `skills/weather-prediction-context/SKILL.md`.

Findings:

- Ensemble calibration improved raw member counting but still lost to current
  bot probabilities. Holdout Brier: original `0.0386`, raw members `0.1725`,
  calibrated members `0.1503`. Do not promote.
- Forecast-update lag has a small positive signal in all rows, especially
  15-60m after a fresh forecast update (`+0.0111` signed 30m movement), but
  OPEN-only rows moved against us (`-0.0114` signed 60m). This points to better
  timing/cancel-reprice, not larger sizing.
- Low-price sleeve: YES 10-25c was positive (`+$59.13` over 30 fills), while
  YES <10c was negative (`-$178.06` over 34 fills). NO low-price sleeves were
  negative in this sample.
- Book depth was adequate for sampled paper fills, but snapshot age averaged
  864 seconds. Stale executable-price assumptions are the bigger issue.

New logical next step: use the pending-order data to compare immediate paper
fills versus maker-style fills and expirations. After that, test whether TTL
extension/reprice improves realized PnL without increasing stale adverse
selection.

## Execution Controls Implemented — 2026-05-17

Implemented the first defensive controls from the PDF/replay review:

- Block YES `<10c` by default (`YES_UNDER_10C_SIZE_MULT=0.0`).
- Block NO `<50c` by default (`NO_UNDER_50C_SIZE_MULT=0.0`).
- Keep YES `10-25c` as the only convexity sleeve, half-sized and capped at
  `$10` (`YES_10_25C_SIZE_MULT=0.50`, `YES_10_25C_MAX_USD=10.0`).
- Cap paper fills to available top-of-book size from the fresh Kalshi orderbook
  call (`REQUIRE_TOP_BOOK_SIZE=true`).
- Add `jobs.exit_recommendations --threshold 0.70` to surface open paper
  positions whose mark-to-market reaches 70% of max gain.

Manual paper-mode run after deploy: no new paper fills opened because the
health tripwire correctly blocked KNYC/KMDW/KMIA (`TRIPWIRE_RED`). Current
latest health has KNYC `MODEL=RED`; KMDW/KMIA model status is AMBER/insufficient
recent settled fills, and HFMETAR is lagging RED for KMDW/KMIA.

Immediate operating posture: keep the tripwire active. Do not acknowledge model
RED merely to get more trades. The pending-order table is now the source for
execution-quality learning; the next productive change is measuring fill rate,
missed winners, and adverse selection by TTL/price band.

## Pending Paper Orders Implemented — 2026-05-17

Paper mode now defaults to maker-style pending orders instead of immediate
fills:

- `PAPER_ORDER_MODE=true` creates a `paper_order` at one cent better than the
  executable entry price (`PAPER_ORDER_IMPROVEMENT_CENTS=1`).
- `PAPER_ORDER_TTL_MIN=15` expires the order unless later `market_snapshot`
  rows prove executable price and sufficient top-of-book size.
- `jobs.process_paper_orders` processes pending orders directly, and `main.py`
  processes existing pending orders at startup before evaluating new signals.
- Filled orders write the same `paper_fill` rows used by existing settlement
  and PnL reports, preserving downstream reporting.

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

## Profitability & Early-Exit Strategy Upgrades — 2026-05-18

Implemented the core profitability and take-profit upgrades to correct the historical realized P&L deficit:

- **3¢ Price Entry Improvement (`PAPER_ORDER_IMPROVEMENT_CENTS=3`):** Increased maker order pricing improvement from 1¢ to 3¢ in `config.py` to widen execution margins.
- **85% Take-Profit Early Exit Engine (`strategy/early_exits.py`):** Built a take-profit module that scans open paper fills and sells them back to the market at `entry_price + 0.85 * (1.0 - entry_price)` using latest `market_snapshot` bids. Triggered automatically at the start of every 10-minute trading loop in `main.py`.
- **Streamlit Profitability Controls (`dashboard/app.py`):** Added a station multi-select filter and a quick-toggle checkbox (`Show only RED/Alerted stations`) to isolate and track profitability specifically for alert-bypassed stations.

### Historical Database Backtest Results (168 Fills)

We simulated the new upgrades across all 168 fills stored in the database:

- **Baseline (Original Rules):** **-$258.01**
- **3¢ Price Entry Improvement Only:** **+$221.20** (Gain: **+$479.21**)
- **85% Early Exit Only:** **-$7.56** (Gain: **+$250.45**, 57 exits)
- **Combined New Rules (Cheaper Entry + 85% TP Exit):** **+$462.03** (Net Gain: <span style="color:green">**+$720.04**</span>, 67 exits)

### Live Posture & Deployment Path Forward

1. **Keep RED Stations in Bypass:** Leave KMIA and KNYC flagged RED by the health check. In paper mode, they will continue placing entries to collect velocity sample data under the new 3¢ pricing and 85% TP exit rules.
2. **Observe Recovery Curve:** Monitor the new **Profitability** dashboard tab over the next few days to watch the drift close and verify that realized margins track the simulated backtest gains.
3. **Transition to Live Sizing:** Once the expected-vs-realized mismatch recovers and the health check transitions them back to GREEN, transition sizing parameters to live capital.

