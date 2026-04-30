# Weather Bot — Setup & Run

MVP for Kalshi daily-temperature contracts. Scope: KNYC daily high, paper trading only.

## 1. Environment

```bash
cd weather_bot
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# eccodes (cfgrib dependency) requires the system library on some OSes
# macOS:   brew install eccodes
# Ubuntu:  apt-get install libeccodes0 libeccodes-dev

cp .env.example .env
# edit .env with DATABASE_URL and Kalshi credentials
```

## 2. Database

```bash
# Start a local Postgres (docker-compose or native); then:
psql $DATABASE_URL -f db/schema.sql
python -m weather_bot.data.persistence  # no-op — run explicitly:
python -c "from weather_bot.data.persistence import bootstrap_stations; bootstrap_stations()"
```

Recommended: enable TimescaleDB and convert the `metar_obs` / `det_forecast` tables to hypertables.

## 3. Backfill historical data (bias correction training)

```bash
python -m weather_bot.jobs.backfill_history --days 60
```

Pulls 60 days of NBM 12Z cycles and 60 days of METAR observations. Takes ~20-40 minutes depending on bandwidth.

## 4. Compute bias tables

```bash
python -m weather_bot.jobs.nightly_verify
```

Creates rolling 30-day bias rows in `station_bias`. Run nightly after observations are settled.

## 5. Live data pulls (run on cron)

```cron
# Every hour at :15 — pull latest HRRR cycle
15 * * * * cd /path/to/weather_bot && .venv/bin/python -m weather_bot.jobs.pull_hrrr

# Every 6 hours at :30 — pull latest NBM QMD (cycles at 00/06/12/18Z)
30 3,9,15,21 * * * cd /path/to/weather_bot && .venv/bin/python -m weather_bot.jobs.pull_nbm

# Every 30 minutes — pull METAR observations
*/30 * * * * cd /path/to/weather_bot && .venv/bin/python -m weather_bot.jobs.pull_metar

# Every 15 minutes — refresh Kalshi market list
*/15 * * * * cd /path/to/weather_bot && .venv/bin/python -m weather_bot.jobs.pull_kalshi_markets

# Every 10 minutes during trading hours — produce signals
*/10 6-22 * * * cd /path/to/weather_bot && .venv/bin/python -m weather_bot.main

# Nightly — recompute bias & verification
15 3 * * *  cd /path/to/weather_bot && .venv/bin/python -m weather_bot.jobs.nightly_verify
```

## 6. Paper-trade checklist

- [ ] 14 days of clean NBM + METAR data
- [ ] Bias tables populated with sample_size >= 10 per (month, lead)
- [ ] 7+ days of signals logged, reliability diagram plotted
- [ ] Brier < baseline (climatology)
- [ ] Aggregate paper PnL net of fees > 0 over 4 weeks

Only flip `PAPER_MODE=false` in `.env` after all five boxes are checked.

## 7. Smoke tests

```bash
pytest weather_bot/tests -q
```

## Architecture

```
weather_bot/
├── config.py              # stations, constants, env
├── db/schema.sql
├── data/
│   ├── grib_utils.py      # S3 byte-range + GRIB2 parsing
│   ├── nbm_fetcher.py     # NBM QMD probabilistic
│   ├── hrrr_fetcher.py    # HRRR hourly deterministic
│   ├── metar_fetcher.py   # aviationweather.gov
│   └── persistence.py     # psycopg3 DB layer
├── models/
│   ├── bias_correction.py # rolling per-station bias
│   └── distribution.py    # piecewise CDF + bucket probs
├── strategy/
│   ├── kalshi_client.py   # RSA-signed REST client
│   ├── kalshi_parser.py   # event/bucket parser
│   └── ev.py              # edge, fees, Kelly sizing
├── verification/
│   └── metrics.py         # Brier / CRPS / reliability
├── jobs/                  # cron entrypoints
└── main.py                # signal orchestrator
```
