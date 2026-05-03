# weatherbot

Probabilistic trading bot for Kalshi daily-temperature contracts. Builds a calibrated forecast distribution from NOAA NBM (probabilistic) and HRRR (deterministic) models, applies station-level bias correction, and computes per-bucket fair probabilities for Kalshi range markets. Sizes positions with a fractional Kelly under fee-aware EV.

Runs in paper mode by default. NYC (KNYC) only in current scope.

## How it works

```
NOAA S3 (NBM QMD + HRRR)         METAR (aviationweather.gov / IEM)
        │                                    │
        ▼                                    ▼
  data/nbm_fetcher.py              data/metar_fetcher.py
  data/hrrr_fetcher.py                       │
        │                                    ▼
        ▼                              daily_obs (Postgres)
  prob_forecast (Postgres)                   │
        │                                    │
        └──────────┬─────────────────────────┘
                   ▼
        models/bias_correction.py   (rolling 30-day per-station bias)
                   │
                   ▼
        models/distribution.py      (piecewise CDF + HRRR blend + intraday floor/ceiling)
                   │
                   ▼
            P(lo ≤ T < hi)   for each Kalshi range bucket
                   │
                   ▼
        strategy/ev.py              (Kalshi fees, edge, Kelly sizing, divergence guardrail)
                   │
                   ▼
                main.py             (signal orchestrator → paper_fill)
```

Settled fills get reconciled against METAR observations each morning via `jobs/settle_paper_fills.py`.

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
pytest tests/ -q
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
*/15 * * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_kalshi_markets

# Pull METAR observations
*/30 * * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_metar

# NBM QMD publishes 4× daily (00/06/12/18Z), ~3h after cycle time
30 3,9,15,21 * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_nbm

# HRRR publishes hourly
15 * * * *  cd /path/to/weatherbot && .venv/bin/python -m weather_bot.jobs.pull_hrrr

# Signal generator: every 15 min during waking hours (NYC time)
*/15 6-22 * * *  cd /path/to/weatherbot && ./tick.sh

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
| `jobs.pull_metar` | Pull observations from aviationweather.gov | Every 30 min |
| `jobs.pull_kalshi_markets` | Refresh Kalshi market list + price snapshots | Every 15 min |
| `main` (via `tick.sh`) | Score markets, generate paper fills | Every 15 min when markets are open |
| `jobs.settle_paper_fills` | Reconcile fills against observed temperatures | Once daily, after midnight |
| `jobs.retrain_bias` | Recompute rolling 30-day station bias | Once daily, after settlement |
| `jobs.paper_report` | P&L summary + expected-vs-realized edge | Once daily, after retrain |
| `jobs.nightly_verify` | Brier / CRPS / reliability metrics | Once daily (slow) |
| `jobs.health_check` | Hourly tripwire (DATA/MODEL/MARKETS/RISK/PNL → GREEN/AMBER/RED) | Every 30 min |
| `jobs.bias_drift` | Snapshot bias + flag >2σ overnight moves | Once daily, after retrain |
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

A live read of bot health, calibration, trading state, and deep-dive tools
runs at `http://127.0.0.1:8501` once the dashboard launchd agent is loaded.

```bash
# Start manually (foreground, for development)
.venv/bin/streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501

# Or run as a launchd agent (auto-start at login, restart on crash)
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.walter.weatherbot-dashboard.plist
```

Four tabs:

- **Status** — five-second health check. Six tiles (DATA, MODEL, MARKETS, RISK,
  P&L, ALERTS) green/amber/red. Click "Ack" to clear a RED alert and unblock
  the trade loop.
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
