# research/ — viability prototype for new data sources

Walled-off sandbox for evaluating whether to add new data sources to the bot.
**Nothing here writes to the production DB schema or runs from `main.py`.**
Output is CSV + markdown reports in `research/reports/` that you read once,
decide on, and either promote pieces into `data/` + `models/` or drop.

## Question this layer answers

> Should we integrate NWS CLI/DSM and ECMWF/GFS forecasts into the bot?

Three sub-questions, three scripts:

| Script | Question |
|---|---|
| `compare_observations.py` | Does our METAR-reconstructed daily TMAX/TMIN match what NWS publishes (and uses for Kalshi settlement)? |
| `compare_forecasts.py`    | Are ECMWF / GFS competitive with NBM / HRRR at lead-1 TMAX accuracy? |
| (manual)                  | Does adding ECMWF/GFS to the ensemble change bucket probabilities enough to flip trade decisions? |

The third is a counterfactual that builds on the first two — defer until they have signal.

## Sources

| Source | Fetcher | Notes |
|---|---|---|
| **NWS CLI** (Daily Climate Report) | `sources/nws_text_products.py` | Settlement authority for Kalshi NHIGH per the rule sheet. Forecaster-reviewed. Issued ~6–7 AM ET. |
| **NWS DSM** (Daily Summary Message) | `sources/nws_text_products.py` | Automated ASOS, issued ~midnight LST. Use as **early preview** of CLI; cross-check for Kalshi's 11 AM delay scenario. |
| **ECMWF IFS** (0.25°) | `sources/openmeteo_fetcher.py` | Via Open-Meteo. Accuracy leader at 3–7 day leads in the literature. |
| **GFS** (0.25°) | `sources/openmeteo_fetcher.py` | Via Open-Meteo. Already familiar; cheaper alternative to direct NOAA S3 GRIB ingestion. |

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

# Compare NBM/HRRR/ECMWF/GFS forecast accuracy at lead day 1
python -m research.compare_forecasts --days-back 30 --lead-days 1

# Multi-lead sweep
python -m research.compare_forecasts --days-back 30 --lead-days 0 1 2
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

If `compare_forecasts.py` shows ECMWF / GFS clearly outperform or are
complementary to NBM / HRRR, promote `sources/openmeteo_fetcher.py` → `data/`
and add a blend factor in `models/distribution.py`. **Do not modify
distribution.py until that signal is established.**

If the comparison shows no improvement, leave the bot alone and delete the
fetchers — that's also a successful outcome of this layer.
