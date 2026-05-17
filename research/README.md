# research/ — viability prototype for new data sources

Walled-off sandbox for evaluating whether to add new data sources to the bot.
**Nothing here writes to the production DB schema or runs from `main.py`.**
Output is CSV + markdown reports in `research/reports/` that you read once,
decide on, and either promote pieces into `data/` + `models/` or drop.

## Question this layer answers

> Should we integrate NWS CLI/DSM, ECMWF/GFS, Open-Meteo true ensembles, and WeatherNext forecasts into the bot?

Three sub-questions, three scripts:

| Script | Question |
|---|---|
| `compare_observations.py` | Does our METAR-reconstructed daily TMAX/TMIN match what NWS publishes (and uses for Kalshi settlement)? |
| `compare_hfmetar.py`      | Would switching daily TMAX from hourly METAR to 5-min MADIS HFMETAR close the gap to CLI? |
| `compare_forecasts.py`    | Are ECMWF / GFS / WeatherNext competitive with NBM / HRRR at lead-1 TMAX accuracy? |
| `jobs.shadow_ensemble_report` | Do true ensemble member probabilities improve calibration versus the current signal probabilities? |

The third is a counterfactual that builds on the first two — defer until they have signal.

## Sources

| Source | Fetcher | Notes |
|---|---|---|
| **NWS CLI** (Daily Climate Report) | `sources/nws_text_products.py` | Settlement authority for Kalshi NHIGH per the rule sheet. Forecaster-reviewed. Issued ~6–7 AM ET. |
| **NWS DSM** (Daily Summary Message) | `sources/nws_text_products.py` | Automated ASOS, issued ~midnight LST. Use as **early preview** of CLI; cross-check for Kalshi's 11 AM delay scenario. |
| **ECMWF IFS** (0.25°) | `sources/openmeteo_fetcher.py` | Via Open-Meteo. Accuracy leader at 3–7 day leads in the literature. |
| **GFS** (0.25°) | `sources/openmeteo_fetcher.py` | Via Open-Meteo. Already familiar; cheaper alternative to direct NOAA S3 GRIB ingestion. |
| **Open-Meteo true ensembles** | `data/openmeteo_ensemble_fetcher.py` | GFS, ECMWF IFS, and ECMWF AIFS member rows. Stored in `ensemble_forecast`; shadow-only. |
| **WeatherNext 2** (0.25°, 64-member ensemble) | `sources/weathernext_fetcher.py` | Optional BigQuery adapter. Requires Google WeatherNext data request + Analytics Hub subscription; provides ensemble TMAX p10/p50/p90 once configured. |

Both NWS products go through `https://api.weather.gov/products`. Open-Meteo
historical archive is `historical-forecast-api.open-meteo.com/v1/forecast`.

## Usage

Run from the repo root with the venv active:

```bash
# Smoke test the fetchers against today's data
python -m research.sources.nws_text_products --station KNYC --type BOTH
python -m research.sources.openmeteo_fetcher  --station KNYC --target-date 2026-04-15 --historical

# Compare CLI vs DSM vs METAR for the past 30 days, all fetch stations
python -m research.compare_observations --days-back 30

# Compare hourly METAR vs 5-min HFMETAR against CLI ground truth
# (requires cli_obs populated — run pull_cli --days-back 30 first if sparse)
python -m research.compare_hfmetar --days-back 30

# Compare NBM/HRRR/ECMWF/GFS forecast accuracy at lead day 1
python -m research.compare_forecasts --days-back 30 --lead-days 1

# Include WeatherNext 2 after subscribing to the BigQuery dataset
export WEATHERNEXT_BQ_TABLE="your-project.your_dataset.weathernext_2_0_0"
python -m research.compare_forecasts --days-back 30 --lead-days 1 --include-weathernext

# Multi-lead sweep
python -m research.compare_forecasts --days-back 30 --lead-days 0 1 2

# Stored-source benchmark from production DB rows only
python -m weather_bot.jobs.forecast_benchmark_report --days-back 30

# Pull true ensemble member rows, then replay strict as-of probabilities
python -m weather_bot.jobs.pull_ensemble

# Backfill recent WeatherNext 2 cycles for strict as-of replay.
# Keep this bounded because it queries BigQuery.
python -m weather_bot.jobs.backfill_weathernext --days-back 7 --stations KNYC,KMDW,KMIA --horizon-days 3

# Shadow-only ensemble replay; does not affect live/paper trading
python -m weather_bot.jobs.shadow_ensemble_report --days-back 30

# Test EMOS-lite bias/spread transforms for true ensemble member probabilities
python -m weather_bot.jobs.ensemble_calibration_report --days-back 30

# Test whether market prices lag fresh forecast/significant probability edge
python -m weather_bot.jobs.forecast_update_lag_report --days-back 30 --limit 2500

# Generate a deterministic AI-readable context brief for one station/date
python -m weather_bot.jobs.ai_context_brief --station KNYC --valid-date 2026-05-16

# Flag open paper positions that have reached the early-exit threshold
python -m weather_bot.jobs.exit_recommendations --threshold 0.70
```

Output goes to `research/reports/{obs_compare,fc_compare}_<date>.{csv,md}`.

## Caveats

1. **Open-Meteo historical-forecast-api archives the best-available forecast for
   a target date but doesn't expose run-time granularity** the way our raw NBM/HRRR
   GRIB ingestion does. So the lead-day filter affects NBM/HRRR (we filter by
   `run_time::date`) but not ECMWF/GFS — they get whatever Open-Meteo's archive
   serves for that target date. This is fine for a first-cut viability check;
   for a precise apples-to-apples backtest we'd swap to direct ECMWF/GFS GRIB.

2. **CLI is the settlement authority but not infallible** — Kalshi rules allow up
   to 11 AM ET delay if CLI disagrees with METAR-reported 6h/24h highs, and revisions
   *during* the statistical period or before expiration may apply. See
   `~/.claude/projects/-Users-walterworley-dev-weather-bot/memory/kalshi_nhigh_settlement_rules.md`.

3. **METAR ground-truth has known gaps** — if the fetcher misses certain hours
   you'll see |CLI−METAR| diffs >2°F. That's a finding, not a bug in this
   research layer.

## What promotes out of here

If `compare_observations.py` shows CLI/DSM differ meaningfully from METAR, promote
`sources/nws_text_products.py` → `data/nws_text_products.py` and add tables/jobs
for ongoing capture. Reconciliation in `jobs/settle_paper_fills.py` should then
prefer CLI over METAR-derived TMAX.

If `compare_forecasts.py` shows ECMWF / GFS / WeatherNext clearly outperform or
are complementary to NBM / HRRR, promote the source into the live ensemble and
add a blend factor in `models/distribution.py`. **Do not modify distribution.py
until that signal is established.**

For live-captured challenger models, prefer
`jobs.forecast_benchmark_report` over the Open-Meteo historical comparison.
Then use `jobs.shadow_ensemble_report` to test probability/reliability impact
before changing `main.py` or `models/distribution.py`. True ensemble rows are
eligible for promotion only after strict as-of replay beats the current signal
probabilities on at least 50 settled signals.

If the comparison shows no improvement, leave the bot alone and delete the
fetchers — that's also a successful outcome of this layer.

## 2026-05-16 WeatherNext 2 calibration pass

WeatherNext 2 is configured on the VPS and writes member-hour rows as
`WEATHERNEXT2` in `ensemble_forecast`. A bounded 7-day backfill for
KNYC/KMDW/KMIA at a 3-day horizon loaded 27 published cycles from
2026-05-10 00Z through 2026-05-16 12Z; 2026-05-16 18Z was not yet published.

Source counts after the pass:

| model | rows | stations | runs | first run | last run |
|---|---:|---:|---:|---|---|
| GFS_ENS | 127,782 | 5 | 5 | 2026-05-15 18Z | 2026-05-16 18Z |
| ECMWF_IFS_ENS | 210,222 | 5 | 5 | 2026-05-15 18Z | 2026-05-16 18Z |
| ECMWF_AIFS_ENS | 210,222 | 5 | 5 | 2026-05-15 18Z | 2026-05-16 18Z |
| WEATHERNEXT2 | 68,864 | 5 | 27 | 2026-05-10 00Z | 2026-05-16 12Z |

Strict shadow replay on 2026-05-16: 1,200 rows, original Brier 0.0947,
WeatherNext/true-ensemble shadow Brier 0.1925 (`+0.0978`, worse). Only KMDW
lead-1 improved (`-0.0113` Brier); every other station/lead slice was worse.

Decision: keep WeatherNext shadow-only. Do not promote it into
`models/distribution.py` or live sizing as a direct member-probability source.
The next useful experiment is not "use WeatherNext wholesale"; it is a
station/lead-gated challenger, starting with KMDW lead-1, plus reliability bins
once more settled signals accumulate.

## 2026-05-17 PolymarketWeather-inspired research pass

The PolymarketWeather article's useful idea is that raw ensemble counting needs
probability calibration and that market prices can lag forecast updates. Added:

- `research/ensemble_calibration.py` and `jobs/ensemble_calibration_report.py`
  for EMOS-lite bias/spread replay.
- `research/forecast_update_lag.py` and `jobs/forecast_update_lag_report.py`
  for probability-edge z buckets and signed market movement after signals.
- `research/ai_context_brief.py`, `jobs/ai_context_brief.py`, and
  `skills/weather-prediction-context/SKILL.md` for an advisory AI context lane.

Backtest findings on the VPS:

| report | result |
|---|---|
| Calibrated ensemble replay | Best train transform was `bias=+2.0,spread=1.00`; on chronological holdout, raw member Brier improved from 0.1725 to 0.1503, but the bot's original probability was still far better at 0.0386. |
| Forecast-update lag | 2,500 recent signals had small positive signed movement overall: +0.0027 at 15m, +0.0038 at 30m, +0.0037 at 60m. Strongest age bucket was 15-60m after a fresh forecast: +0.0082, +0.0111, +0.0068. |
| OPEN-only lag | OPEN signals moved against us after entry: -0.0047 at 15m, -0.0063 at 30m, -0.0114 at 60m. This argues for TTL/reprice discipline before larger sizing. |
| Low-price convexity | YES 10-25c was profitable (+$59.13 over 30 fills); YES <10c was bad (-$178.06 over 34 fills). NO low-price sleeves were bad in the current sample. |
| Book depth | Top-of-book was not too small for paper fills in the sampled fills, but average prior snapshot age was 864 seconds, so execution freshness is the weak spot, not size. |

Decision: do not promote raw or calibrated true ensembles into production yet.
Do not add a blanket z-score gate yet. The actionable next engineering change is
an execution TTL/reprice rule plus a narrowly monitored YES 10-25c convexity
sleeve. The AI context lane is advisory only and should output
`context_supports`, `context_warns`, or `insufficient_context`.

The first defensive controls are now in the trading loop: YES `<10c` and NO
`<50c` are blocked by default, YES `10-25c` is half-sized and capped at `$10`,
and paper fills cannot exceed fresh top-of-book size. These are protective
entry-shaping controls, not proof that the strategy is fixed.

## 2026-05-17 pending paper-order execution model

Paper mode now records maker-style `paper_order` rows before writing
`paper_fill`. Each OPEN signal places a one-cent-better limit order with a
15-minute TTL. `jobs.process_paper_orders` fills only when later Kalshi
`market_snapshot` rows show executable price and enough top-of-book size; stale
or undersized opportunities expire.

This gives us the missing denominator for execution research: fills, expiries,
missed winners, and adverse selection by station/lead/side/price band. The next
report should compare immediate-signal PnL to pending-order PnL and ask whether
reprice/TTL extension improves net results.

## 2026-05-03 finding: HFMETAR vs hourly METAR

`compare_hfmetar.py` over 30 days, 81 paired CLI+METAR station-days:

| Station | Paired days | hourly mean abs err | HFMETAR mean abs err | Helped / hurt / tied |
|---|---|---|---|---|
| KNYC | 30 | 0.87°F | 0.87°F | 0 / 0 / 30 |
| KMDW | 30 | 0.73°F | 0.53°F | 16 / 8 / 6 |
| KMIA | 21 | 0.81°F | 0.37°F | 11 / 5 / 5 |

Key result is the **signed mean** shift: hourly systematically undercounts CLI
by +0.73 to +0.87°F (the :53 reading misses intra-hour peaks). HFMETAR collapses
that bias to ±0.05°F at the ASOS stations.

KNYC is unchanged because it's a coop site, not ASOS — see
`memory/knyc_no_hfmetar.md`. Don't expect HFMETAR to help there ever.

Promotion path: swap `iem_fetcher.fetch_historical()` for
`fetch_historical_5min()` in `metar_fetcher.backfill()` for KMDW + KMIA. Keep
KNYC on hourly. Bias correction will need retraining since the systematic
+0.7°F miss it currently corrects for would no longer be present.
