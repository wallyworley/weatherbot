# Forecast Model Roadmap - 2026-06-05

Purpose: document what we are doing, why we are doing it, and what we should
add next after reviewing external weather-model guidance for prediction-market
bots. This is an execution roadmap for `weather_bot`, not a claim that any new
source has edge yet.

## Current conclusion

The bot still has no demonstrated live edge. The binding problem is forecast
center accuracy in the morning, not calibration, not exit tuning, and not
station whitelisting. The promotion gate remains:

> A change may affect live probabilities or sizing only after it improves
> morning Brier/RPS skill versus the market out of sample.

Recent diagnosis:

- Market-relative morning skill is negative.
- RPS made the failure sharper: misses are centered wrong, not merely
  under-confident.
- Reliability decomposition showed our calibration is already acceptable; our
  deficit is information/resolution.
- Raw-vs-calibrated testing did not show the calibrator as the cheap fix.
- Therefore the research lane is about improving the meteorological center and
  proving it out of sample.

## What we already collect

These sources are now collected or already existed in the bot:

| Source | Table | Role | Status |
|---|---|---|---|
| NBM QMD percentiles | `prob_forecast` | Probabilistic distribution shape and p50 center | Production input |
| HRRR | `det_forecast` | Same-day high-resolution deterministic center | Production/shadow input |
| GFS | `det_forecast` | Global deterministic center, especially lead >= 1 | Production/shadow input |
| ECMWF via Open-Meteo | `det_forecast` | Independent global deterministic comparator | Shadow/research |
| NWS Grid | `forecast_guidance` | NWS point/grid forecast center | Shadow/research |
| NWS PFM | `forecast_guidance` | Human/NWS text matrix max/min guidance | Shadow/research |
| LAMP | `forecast_guidance` | Hourly station-level MOS/LAMP temperature guidance | Shadow/research |
| MAV | `forecast_guidance` | GFS MOS station guidance | Shadow/research |
| OBS tracker | `forecast_guidance` | High-so-far settlement context | Monitor/context |
| METAR/HFMETAR | `metar_obs` | Intraday observations and high-so-far | Production input |
| WeatherNext 2 | `ensemble_forecast` | Google/AI ensemble research lane | Shadow/research |

The v2 dashboard Forecast Lab now monitors source freshness, source coverage,
forecast-center disagreement, recent source MAE/bias, and live Kalshi station
guidance coverage. It must stay a research dashboard, not a trading approval
screen.

## External model note - evaluated

The Reddit/model-cheat-sheet claim is directionally useful but not sufficient.
Its strongest idea is that station-level prediction often needs regional,
high-resolution or station-specific guidance, because coarse global grids can
miss terrain, coast, valley, urban, and marine-layer effects.

Verified/supporting public references:

- NOAA HRRR: real-time 3 km, hourly updated, cloud-resolving/convection-allowing,
  with radar assimilation.
  <https://rapidrefresh.noaa.gov/hrrr/>
- NOAA LAMP: station guidance for 2-meter temperature and other elements,
  hourly for most elements, covering more than 2000 stations and up to about 38h
  for most elements.
  <https://vlab.noaa.gov/web/mdl/lamp>
- Open-Meteo docs: exposes multiple global and regional models, including HRRR,
  ICON-D2, AROME, ECMWF, and has Historical Forecast and Single Runs APIs for
  past forecast runs.
  <https://open-meteo.com/en/docs>
- Open-Meteo GFS/HRRR docs: JSON access to GFS/HRRR/NAM-style products by
  coordinate.
  <https://open-meteo.com/en/docs/gfs-api>
- NOAA RRFS: next-generation rapid-refresh high-resolution model, 3 km across
  North America, transitioning to operations in 2026.
  <https://gsl.noaa.gov/rrfs/>
- NWS Service Change Notice listing: SCN 26-48 announces RRFS/REFS
  implementation effective August 31, 2026.
  <https://preview.weather.gov/notification/>

Important caveat: "use high-res" is not alpha by itself. The market may already
use the same sources. We only get alpha if our source handling, station mapping,
timing, blending, or regime conditioning produces better probability estimates
than the market in the morning.

## US/Kalshi model priority

For the current Kalshi station-high bot, prioritize this order:

1. Keep NBM as the distribution scaffold.

   NBM is the only source we use that gives a calibrated percentile
   distribution. It should remain the shape/spread source unless a true ensemble
   beats it out of sample.

2. Score official/station guidance as alternate centers.

   NWS Grid, PFM, LAMP, and MAV are now collected. They should be scored as
   candidate centers against final highs and against the morning market, not
   promoted directly.

3. Re-score HRRR by station/regime, not globally.

   HRRR is the correct same-day high-resolution US model in principle, but our
   own broad HRRR/GFS blend work has not beaten the market. Use it where it
   helps by station/regime; do not assume it helps everywhere.

4. Use Open-Meteo only where it preserves point-in-time integrity.

   Open-Meteo is valuable plumbing, especially for JSON access and historical
   model runs. The key requirement is "what was issued at that time," not a
   stitched best-available historical forecast that leaks later information.

5. Watch RRFS/REFS.

   RRFS/REFS is likely the next important US high-resolution source. It should
   be added as a shadow retriever once public operational files are stable and
   the point-in-time run/valid-time mapping is clear.

6. Treat METAR.ws as a latency experiment only.

   A low-latency METAR websocket could help with intraday high-so-far and SPECI
   detection, but it is not a forecast model. It must be benchmarked against
   AviationWeather/IEM for first-seen time, missed reports, duplicate handling,
   and temp parsing before relying on it.

## Non-US model priority

This matters only if we expand into Polymarket/global weather, because Kalshi
currently uses US NWS station markets.

| Region | Candidate source | Why it matters | Current action |
|---|---|---|---|
| France/Western Europe | AROME | High-resolution regional guidance for fog, convection, local effects | Defer until non-US trading |
| Germany/Central Europe/Alps | ICON-D2 | Regional high-res model for Central Europe | Defer |
| Switzerland/Alps | ICON-CH1-EPS | Very high-resolution Alpine/valley effects | Defer |
| Netherlands/Nordics | HARMONIE-AROME | Regional high-res for local effects | Defer |
| UK airports | UKV | 1.5 km Met Office inner-domain model | Defer |
| Global multi-day | ECMWF/GFS | Background synoptic context | Already shadowing ECMWF/GFS where practical |

Do not add these to the current Kalshi production path unless we start trading
markets that settle on those regions.

## Research gates

Every forecast source or blend must pass the same evaluation ladder:

1. Parse correctness

   Validate product layout with regression tests. We already hit this with MAV
   3-digit temperatures and LAMP `:00` aviation-only products.

2. Station mapping correctness

   Source station must match the market settlement station. Kalshi station
   guardrail is now active for the 19 live stations.

3. Point-in-time correctness

   Store `run_time`, `valid_time`, `valid_date`, `ingested_at`, and source
   metadata. Historical scoring must use only rows available at signal time.

4. Center accuracy

   Compare source high center versus CLI/daily truth by station, lead, and
   regime. Use MAE/bias as a diagnostic only.

5. Market-relative probability skill

   Convert candidate centers into bucket probabilities using a consistent
   distribution shape, then score morning Brier/RPS versus market. This is the
   real gate.

6. Out-of-sample watchlist

   Positive pockets must be watched prospectively for fresh days/weeks before
   any sizing changes.

## Near-term tasks

Status update 2026-06-06:

- `deb_recent_mae_center` is now a shadow variant in
  `research/morning_center_ablation.py`. It uses recent point-in-time source
  MAE to blend available centers and must still pass morning market-relative
  Brier/RPS out of sample before any production use.
- `TAF` is now collected as research features in `forecast_guidance`, with
  `TAF_SUPPRESSION_SCORE` and `TAF_WIND_SHIFT_SCORE`. It is a regime feature,
  not a temperature center.
- `research/madis_hfmetar_benchmark.py` compares direct NOAA MADIS HFMETAR
  against the existing IEM recent feed for latency, missingness, and latest
  temperature agreement.

### 1. Finish source-by-source PIT scoring

Create or extend the ablation scorer so these variants are first-class:

- `logged_model`
- `rebuilt_prod`
- `nbm_only`
- `hrrr_center`
- `gfs_center`
- `ecmwf_center`
- `nws_grid_center`
- `pfm_center`
- `lamp_peak_center`
- `mav_center`
- future: `rrfs_center`

Output:

- Morning Brier skill vs market.
- Morning RPS skill vs market.
- Source MAE/bias by station.
- Reliability/resolution decomposition where applicable.
- Separate views for high market uncertainty.

### 2. Add source disagreement watchlist

Forecast Lab already shows center disagreement. Next useful improvement:

- Flag stations where at least three sources exist and spread >= 4 degF.
- Record those station-days into a durable research table or report.
- After settlement, score which source was closest.

This gives us a clean dataset of "days with something to learn."

### 3. Add Open-Meteo Single Runs audit

Goal: determine whether Open-Meteo can supply strict point-in-time historical
runs for GFS/HRRR/ECMWF without look-ahead bias.

Questions:

- Can we request a specific model initialization time?
- Are returned hourly values exactly as issued at that run?
- What is model availability lag?
- Does the API expose enough metadata to store run_time reliably?
- Does it cover all 19 Kalshi settlement stations cleanly?

If yes, add a shadow retriever. If no, keep current direct NOAA/Open-Meteo
deterministic fetches and avoid using historical Open-Meteo for strict scoring.

### 4. Add RRFS watcher

Create a small discovery job after operational data availability is stable:

- Check public NOMADS/NOAA object layout.
- Confirm run cadence and forecast horizon.
- Extract TMP_2M for station points.
- Store as `det_forecast(model='RRFS')` or a dedicated `forecast_guidance`
  source.
- Do not use in production until scored.

### 5. METAR latency bakeoff

Benchmark METAR.ws, AviationWeather, and IEM:

- First-seen latency by station.
- Missing observation rate.
- Duplicate/revision behavior.
- SPECI coverage.
- Temperature parsing agreement.
- Uptime over at least one week.

Only use a websocket feed as an additional observation source if it is faster
and reliable. Keep AviationWeather/IEM as fallback.

## What not to do

- Do not size up because a new model is higher resolution.
- Do not replace NBM distribution shape with a deterministic model.
- Do not treat a station/regime pocket as real until it survives fresh
  out-of-sample days.
- Do not score historical Open-Meteo blended archive data as if it were
  point-in-time unless Single Runs proves that.
- Do not add Europe/UK regional models to the Kalshi bot unless the market
  universe expands beyond US stations.

## Operating rule

The working thesis is now:

> Alpha, if it exists, comes from a better morning station-level center,
> transformed into calibrated bucket probabilities, and proven against the
> market out of sample.

Everything else is instrumentation, diagnosis, or damage control.
