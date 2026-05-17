# weatherbot

Probabilistic trading bot for Kalshi daily-temperature contracts. Builds a calibrated forecast distribution from NOAA NBM (probabilistic), HRRR, and GFS inputs, captures ECMWF/Open-Meteo true ensemble members as a shadow challenger lane, applies station-level bias correction, and computes per-bucket fair probabilities for Kalshi range markets. Sizes positions with a fractional Kelly under fee-aware EV.

Runs in paper mode by default. Current trade scope is KNYC, KMDW, and KMIA, with the pre-trade bias gate blocking any station whose calibration is missing, thin, or stale.

## How it works

```
NOAA/Open-Meteo (NBM + HRRR + GFS + ECMWF + ensemble members)      METAR + NWS CLI
        │                                    │
        ▼                                    ▼
  data/nbm_fetcher.py              data/metar_fetcher.py
  data/hrrr_fetcher.py                       │
  data/gfs_fetcher.py
  data/ecmwf_fetcher.py                    ▼
  data/openmeteo_ensemble_fetcher.py
        │                         daily_obs / cli_obs (Postgres)
        ▼
  prob_forecast / det_forecast / ensemble_forecast (Postgres)
        │                                    │
        └──────────┬─────────────────────────┘
                   ▼
        models/bias_correction.py   (rolling 30-day per-station bias)
                   │
                   ▼
        models/distribution.py      (piecewise CDF + deterministic blend + intraday floor/ceiling)
                   │
                   ▼
            P(lo ≤ T < hi)   for each Kalshi range bucket
                   │
                   ▼
        strategy/ev.py              (Kalshi fees, edge, Kelly sizing, divergence guardrail)
                   │
                   ▼
                main.py             (signal orchestrator → paper_order → paper_fill)
```

Settled fills get reconciled each morning via `jobs/settle_paper_fills.py`, preferring NWS CLI settlement observations and falling back to METAR-derived daily observations when CLI is not captured yet.

## Current calibration methodology

The trading model uses a piecewise CDF built from NBM QMD percentiles, then
applies station bias correction, deterministic-model blending, and intraday
conditioning.

Current spread inflation is lead-aware, not a blanket multiplier:

| Lead day | Variance multiplier | Max widening cap |
|---:|---:|---:|
| 0 | 1.00 | 1.10 |
| 1 | 1.25 | 1.35 |
| 2 | 1.15 | 1.25 |
| 3+ | 1.05 | 1.15 |

Important rules:

- Compute `lead_day` with `lead_day_for_station(...)` so station-local dates
  are respected.
- Evaluate calibration with side-adjusted fair probability:
  `YES -> fair_prob`, `NO -> 1 - fair_prob`.
- Treat `paper_fill.payout > 0` as a side-relative win.
- Compute Kalshi fees with `fee_for_order(price, contracts)`, not by
  multiplying a rounded one-contract fee.
- Live probability calibration is signal-based and event-weighted. It uses
  logged signals whose markets have known CLI/daily outcomes, then falls back
  through `station+lead+bucket -> lead+bucket -> station+bucket -> global`.
  Repeated scores for the same ticker/bin contribute one effective event.
- Default empirical calibration is intentionally conservative:
  `PROB_CALIBRATION_MIN_BUCKET_N=20`, `PROB_CALIBRATION_PRIOR_N=35`, and
  `PROB_CALIBRATION_MAX_DELTA=0.15`.

Current profitability controls are enabled by default and can be overridden in
`.env`:

```dotenv
PROFIT_CONTROLS_ENABLED=true
PAUSED_TRADE_STATIONS=KMDW
KNYC_L1_SIZE_MULT=0.25
NO_UNDER_50C_SIZE_MULT=0.0
YES_UNDER_10C_SIZE_MULT=0.0
YES_10_25C_SIZE_MULT=0.50
YES_10_25C_MAX_USD=10.0
YES_25_50C_SIZE_MULT=0.50
REQUIRE_TOP_BOOK_SIZE=true
PAPER_ORDER_MODE=true
PAPER_ORDER_IMPROVEMENT_CENTS=1
PAPER_ORDER_TTL_MIN=15
```

These controls pause KMDW new entries, quarter-size KNYC day-ahead entries,
block the weakest low-price bands, keep only a capped YES 10-25c convexity
sleeve, and make paper mode wait for a one-cent-better executable order before
writing a fill.

Useful validation commands:

```bash
.venv/bin/python research/profile_calibration.py --start-date 2026-04-01 --end-date 2026-05-06
.venv/bin/python research/backtest_variance_fix.py --start 2026-04-01 --end 2026-05-06
.venv/bin/python research/monitor_edge_accuracy.py --hours 1000
.venv/bin/python -m weather_bot.jobs.profitability_report --days-back 30
.venv/bin/python -m weather_bot.jobs.shadow_ensemble_report --days-back 30
```

## True ensemble modeling

The production trading path still uses the calibrated NBM-derived distribution.
True ensemble members are captured separately in `ensemble_forecast` and are
research-only until they beat the current signal probabilities on settled
signals.

Current sources:

- `GFS_ENS` via Open-Meteo `gfs025` (control + perturbed GEFS members)
- `ECMWF_IFS_ENS` via Open-Meteo `ecmwf_ifs025`
- `ECMWF_AIFS_ENS` via Open-Meteo `ecmwf_aifs025`
- `WEATHERNEXT2` via Google WeatherNext 2 BigQuery / Analytics Hub once
  `WEATHERNEXT_BQ_TABLE` and Google application credentials are configured
- Polymarket read-only daily-temp snapshots for KLGA/KORD comparison markets
  are stored in `external_market_snapshot`; they do not affect trading.

Run once or schedule on the VPS:

```bash
.venv/bin/python -m weather_bot.jobs.pull_ensemble
.venv/bin/python -m weather_bot.jobs.pull_weathernext --stations KNYC --horizon-days 1
.venv/bin/python -m weather_bot.jobs.shadow_ensemble_report --days-back 30
```

`jobs.shadow_ensemble_report` uses strict as-of ensemble rows when available and
falls back to the older point-forecast blend for older signals. Promotion rule:
keep this shadow-only until it improves Brier/reliability on at least 50 settled
signals, then replay fixed-size P&L before touching `models/distribution.py`.

## Repository layout

```
weather_bot/
├── main.py                  # signal orchestrator (cron entrypoint)
├── config.py                # stations, bankroll, env config
├── tick.sh                  # launchd wrapper for main.py
├── morning.sh               # nightly settlement + bias retrain
├── analyze_pnl.py           # quick P&L summary
├── data/
│   ├── grib_utils.py        # S3 byte-range + GRIB2 parsing
│   ├── nbm_fetcher.py       # NBM QMD probabilistic
│   ├── hrrr_fetcher.py      # HRRR hourly deterministic
│   ├── gfs_fetcher.py       # GFS hourly deterministic via Open-Meteo
│   ├── ecmwf_fetcher.py     # ECMWF hourly deterministic via Open-Meteo
│   ├── openmeteo_ensemble_fetcher.py # GFS/ECMWF true ensemble members
│   ├── metar_fetcher.py     # aviationweather.gov live
│   ├── iem_fetcher.py       # Iowa Environmental Mesonet historical
│   └── persistence.py       # psycopg3 DB layer
├── models/
│   ├── distribution.py      # piecewise CDF + bias shrinkage + HRRR blend
│   └── bias_correction.py   # rolling per-station bias
├── strategy/
│   ├── ev.py                # edge, fees, Kelly sizing
│   ├── kalshi_client.py     # RSA-signed REST client
│   └── kalshi_parser.py     # event/bucket parser
├── jobs/                    # cron-style entrypoints
│   ├── pull_nbm.py
│   ├── pull_hrrr.py
│   ├── pull_metar.py
│   ├── pull_kalshi_markets.py
│   ├── settle_paper_fills.py
│   ├── retrain_bias.py
│   ├── paper_report.py
│   ├── nightly_verify.py
│   └── backfill_history.py
├── verification/            # Brier / CRPS / reliability diagrams
├── tests/                   # pytest unit tests
└── db/schema.sql            # Postgres schema
```

## Prerequisites

| Requirement | Why | Install |
|---|---|---|
| Python 3.11 | Application runtime | `brew install python@3.11` (macOS) / `apt-get install python3.11` (Ubuntu) |
| PostgreSQL 14+ | Stores forecasts, obs, fills | `brew install postgresql@14 && brew services start postgresql@14` |
| `eccodes` | GRIB2 parsing for NBM/HRRR | `brew install eccodes` (macOS) / `apt-get install libeccodes0 libeccodes-dev` (Ubuntu) |
| Kalshi account + API key | Live market data | https://kalshi.com → Account → API |

TimescaleDB extension is optional but recommended for long-running deployments — it converts `metar_obs` and `det_forecast` into hypertables for faster historical queries.

## Installation

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/wallyworley/weatherbot.git
cd weatherbot
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e ".[dev]"
```

If `pip install` fails on `cfgrib`, the `eccodes` system library is missing — install it first.

### 2. Set up the database

```bash
# Create role and database (one-time)
createuser -s weather
createdb -O weather weather_bot
psql -d weather_bot -c "ALTER USER weather WITH PASSWORD 'weather';"

# Apply schema
psql postgresql://weather:weather@localhost:5432/weather_bot -f db/schema.sql
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
# Database
DATABASE_URL=postgresql://weather:weather@localhost:5432/weather_bot

# Kalshi credentials — see https://trading-api.readme.io/reference/authentication
KALSHI_API_KEY_ID=your-uuid-here
KALSHI_PRIVATE_KEY_PATH=/absolute/path/to/kalshi_private_key.pem
KALSHI_BASE_URL=https://api.elections.kalshi.com/trade-api/v2

# Paper mode is the default and intended state until calibration is proven
PAPER_MODE=true
PAPER_ORDER_MODE=true
PAPER_ORDER_IMPROVEMENT_CENTS=1
PAPER_ORDER_TTL_MIN=15

# Risk and sizing
BANKROLL_USD=1000           # nominal bankroll for paper trading
MAX_POSITION_PCT=0.02       # cap any single position at 2% of bankroll
KELLY_FRACTION=0.25         # quarter-Kelly (conservative)
MIN_EDGE_BPS=200            # require at least 2% post-fee edge to trade
```

> **Kill switch:** set `MAX_POSITION_PCT=0` to force every signal to SKIP without stopping data ingestion. Useful for pausing trading during investigation.

### 4. Bootstrap stations

```bash
python -c "from weather_bot.data.persistence import bootstrap_stations; bootstrap_stations()"
```

### 5. Backfill historical data

The bias correction model needs ~30 days of paired forecast + observation data before it produces sensible corrections:

```bash
python -m weather_bot.jobs.backfill_history --days 35
python -m weather_bot.jobs.retrain_bias
```

Takes ~30–90 minutes depending on bandwidth (35 days × 4 NBM cycles × 8 forward target days from NOAA's free public S3 buckets). Safe to interrupt and resume — `prob_forecast` is upsert-keyed so reruns don't duplicate.

### 6. Smoke test

```bash
.venv/bin/python -m pytest tests/ -q
python -m weather_bot.jobs.pull_kalshi_markets    # should print a market count
python -m weather_bot.main                          # one signal-evaluation pass
```

If `main` produces "Evaluating N open markets (paper_mode=True)" and a row of action= decisions, you're ready to run on a schedule.

## Running the bot

The bot is a collection of cron-style jobs, not a long-running daemon. You schedule each job at the cadence its data source publishes.

### Quick manual run

```bash
source .venv/bin/activate
./tick.sh            # one signal-generation pass: pull markets, score, paper-fill
./morning.sh         # end-of-day: settle yesterday's fills, retrain bias, P&L report
```

### Scheduled cron (Linux / generic)

Add to your user crontab (`crontab -e`). Use absolute paths.

```cron
# Pull Kalshi markets and snapshot prices
*/5 * * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_kalshi_markets

# Pull observations. ASOS stations use 5-min HFMETAR; KNYC standard METAR
# remains effectively hourly, but the fast poll keeps ASOS fresh.
*/5 * * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_metar

# NBM QMD publishes 4× daily (00/06/12/18Z), ~3h after cycle time
30 3,9,15,21 * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_nbm

# HRRR publishes hourly
15 * * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_hrrr

# Open-Meteo challengers: latest GFS/ECMWF cycle, stored in det_forecast
20 * * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_gfs
25 * * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_ecmwf

# True ensemble members for shadow modeling, stored in ensemble_forecast
35 */6 * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_ensemble

# Signal generator: every 5 min during waking hours (NYC time)
*/5 6-22 * * *  cd /path/to/weatherbot && ./tick.sh

# Nightly: settle paper fills, retrain bias, P&L report
0 2 * * *  cd /path/to/weatherbot && ./morning.sh
```

### Scheduled launchd (macOS)

For each job above, create a `~/Library/LaunchAgents/com.weatherbot.<job>.plist` with `StartInterval` (seconds) or `StartCalendarInterval` matching the cron cadence, then `launchctl load` it. The bot was developed on macOS launchd; example `.plist` skeletons are easy to derive from the cron lines above.

### Where to find output

- `logs/<job>.err` and `logs/<job>.out` — stderr/stdout from each scheduled run
- `logs/morning/<YYYY-MM-DD>.log` — nightly settlement + P&L report
- DB: `paper_fill` table — every paper trade with entry price, contracts, fees, payout
- `python analyze_pnl.py` — quick summary of realized P&L

## Operational reference

| Job | Purpose | When to run |
|---|---|---|
| `jobs.pull_nbm` | Pull NBM QMD percentile forecasts | Every 6h, ~3h after cycle |
| `jobs.pull_hrrr` | Pull HRRR deterministic forecasts | Hourly |
| `jobs.pull_gfs` | Pull GFS deterministic forecasts via Open-Meteo | Hourly |
| `jobs.pull_ecmwf` | Pull ECMWF deterministic forecasts via Open-Meteo | Hourly |
| `jobs.pull_ensemble` | Pull Open-Meteo GFS/ECMWF ensemble member forecasts for shadow modeling | Every 6h |
| `jobs.pull_weathernext` | Pull WeatherNext 2 member forecasts into `ensemble_forecast` | Every 6h once configured |
| `jobs.pull_metar` | Pull observations: 5-min HFMETAR via IEM for ASOS stations, hourly METAR via aviationweather.gov for KNYC | Every 5 min |
| `jobs.pull_kalshi_markets` | Refresh Kalshi market list + price snapshots | Every 5 min |
| `jobs.pull_polymarket` | Read-only Polymarket KLGA/KORD daily-temp snapshots for cross-platform research | Every 5 min |
| `main` (via `tick.sh`) | Score markets, generate paper fills | Every 5 min when markets are open |
| `jobs.settle_paper_fills` | Reconcile fills against observed temperatures | Once daily, after midnight |
| `jobs.retrain_bias` | Recompute rolling 30-day station bias | Once daily, after settlement |
| `jobs.paper_report` | P&L summary + expected-vs-realized edge | Once daily, after retrain |
| `jobs.nightly_verify` | Brier / CRPS / reliability metrics | Once daily (slow) |
| `jobs.health_check` | Hourly tripwire (DATA/MODEL/MARKETS/RISK/PNL → GREEN/AMBER/RED) | Every 30 min |
| `jobs.bias_drift` | Snapshot bias + flag >2σ overnight moves | Once daily, after retrain |
| `jobs.profitability_report` | Research maker/wait entry, early exits, and divergence skips | Ad hoc |
| `jobs.forecast_benchmark_report` | Benchmark stored NBM/HRRR/GFS/ECMWF forecasts against CLI truth | Ad hoc |
| `jobs.shadow_ensemble_report` | Replay true-ensemble probabilities when available, otherwise point-blend shadow probabilities | Ad hoc |
| `jobs.backfill_history` | One-off historical backfill | Once at setup, then ad hoc |

## Stations

The bot operates on two station lists:

- **`ACTIVE_FETCH_STATIONS`** — fetchers ingest data and bias_correction trains
  per-station tables for everything in this list. Default: `["KNYC", "KMDW", "KMIA"]`.
- **`ACTIVE_TRADE_STATIONS`** — only stations in this list have markets scored
  and paper-filled. Default: `["KNYC", "KMDW", "KMIA"]`.

  Note: Chicago is **KMDW (Midway)**, not KORD (O'Hare) — Kalshi's CHI markets
  resolve on Midway temperatures per the rule sheet.

A station graduates from fetch-only to trade-eligible once its bias table has
`sample_size >= 10` for the current month at lead_day in `{0,1,2}`. Promote by
adding the code to `ACTIVE_TRADE_STATIONS` in `config.py`. The pre-trade bias
gate in `main.py` will short-circuit any signal whose bias cell is missing,
thin (n<10), or stale (>48h) — so a misconfigured promotion fails safely
rather than trading uncalibrated.

## Command center (Streamlit dashboard)

A live read of bot health, profitability, calibration, trading state, and
deep-dive tools runs at `http://127.0.0.1:8501` once the dashboard launchd
agent is loaded.

```bash
# Start manually (foreground, for development)
.venv/bin/streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501

# Or run as a launchd agent (auto-start at login, restart on crash)
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.walter.weatherbot-dashboard.plist
```

Six views:

- **Home** — daily command view: today/yesterday P&L, open-position watchlist,
  signal open/skip counts, blocking alerts, skip reasons, and station snapshot.
- **Status** — five-second health check. Six tiles (DATA, MODEL, MARKETS, RISK,
  P&L, ALERTS) green/amber/red. Click "Ack" to clear a RED alert and unblock
  the trade loop.
- **Profitability** — current guardrail settings, corrected-fee P&L slices by
  station/lead/side/price band, and the latest maker/early-exit/divergence
  replay report.
- **Calibration** — daily expected-vs-realized edge with threshold band,
  reliability diagram, and bias drift events. **This is the tab that would
  have caught the 2026-04-30 calibration collapse on 04-29.**
- **Trading** — open positions with mark-to-market, today's signals (filterable
  by action), and live distribution preview with Kalshi buckets shaded.
- **Deep Dive** — counterfactual replay engine (re-score historical fills
  under hypothetical parameters), NBM cycle inspector, per-fill ledger.

Every tab has an "ℹ️ How to read this tab" expander; every metric has a
tooltip. Toggle help panels off in the sidebar once you've internalised them.

### Text alerts

When the health-check job sees something flip RED, it fires a **native macOS
Notification Center** alert and (optionally) an **iMessage** to a phone
number. Each RED event alerts exactly once — won't fire again until it
resolves and goes red again later.

```dotenv
# .env additions
ALERTS_ENABLED=true              # default true; set false to mute everything
ALERT_PHONE=+15551234567         # optional; iMessage target. Omit for notifications only.
```

For iMessage to actually send, **Messages.app must be configured with iMessage
on this Mac** and the target phone must be reachable via iMessage. On the
first send, macOS will prompt to authorize Messages.app to be controlled
programmatically — accept the dialog and subsequent sends will go through
silently.

To test: inject a synthetic RED row and run alerts manually:

```bash
.venv/bin/python -m weather_bot.jobs.alerts
```

### Autonomy guardrails

The bot will refuse to open new positions on a station when **any** of these
fire — without a human in the loop:

1. **Bias-staleness gate** — `(station, var, month, lead_day)` cell missing,
   has `sample_size < 10`, or `updated_at > 48h` ago. Skip reason: `BIAS_GATE`.
2. **Tripwire RED** — health_check has the station flagged RED on
   MODEL/RISK/PNL, and no human has acked. Skip reason: `TRIPWIRE_RED`.
3. **Divergence guardrail** — `|fair − market_mid| > 0.50`. Skip reason:
   `DIVERGENCE`.
4. **Profitability gate** — blocks paused stations or entries reduced below
   minimum size by profitability controls. Skip reason: `PROFIT_GATE`.

The system will *never* graduate a station to trading or relax a safety rail
on its own — those are explicit `config.py` commits.

## Paper-trade graduation checklist

Don't flip `PAPER_MODE=false` until:

- [ ] 30+ days of clean NBM + METAR data
- [ ] Bias tables populated with sample_size ≥ 10 per (month, lead)
- [ ] 14+ days of signals logged with reliability diagram tracking
- [ ] Brier score lower than climatology baseline
- [ ] Aggregate paper P&L net of fees positive over 4 consecutive weeks
- [ ] Daily expected-vs-realized edge diff stable (no large swings)

## NBM QMD note

The NBM QMD product publishes daily TMAX/TMIN as **18-hour-window aggregated messages** at specific lead hours, not as instantaneous hourly forecasts. The fetcher targets the message family whose window covers the local-day diurnal extreme:

- TMAX window ends at 06:00 UTC of `target_day + 1`
- TMIN window ends at 18:00 UTC of `target_day`

See [data/nbm_fetcher.py](data/nbm_fetcher.py) for details.

## License

Private project. Not licensed for redistribution.
