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

- Python 3.11
- PostgreSQL (local or remote) — TimescaleDB optional but recommended
- `eccodes` system library for GRIB2 parsing
  - macOS: `brew install eccodes`
  - Ubuntu: `apt-get install libeccodes0 libeccodes-dev`
- Kalshi API key (for live market data; trading is paper-only by default)

## Setup

```bash
git clone https://github.com/wallyworley/weatherbot.git
cd weatherbot
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: DATABASE_URL, KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH

psql $DATABASE_URL -f db/schema.sql
python -c "from weather_bot.data.persistence import bootstrap_stations; bootstrap_stations()"
```

## Backfill historical data

The bias correction needs ~30 days of paired forecast + observation data:

```bash
python -m weather_bot.jobs.backfill_history --days 35
python -m weather_bot.jobs.retrain_bias
```

Takes ~30-90 minutes on first run depending on bandwidth (35 days × 4 NBM cycles × 8 forward target days, all from NOAA's free public S3).

## Cron schedule

Suggested cron entries (or launchd `.plist` equivalents on macOS):

```cron
*/15 * * * *      .venv/bin/python -m weather_bot.jobs.pull_kalshi_markets
*/30 * * * *      .venv/bin/python -m weather_bot.jobs.pull_metar
30 3,9,15,21 * * * .venv/bin/python -m weather_bot.jobs.pull_nbm
15 * * * *        .venv/bin/python -m weather_bot.jobs.pull_hrrr
*/15 6-22 * * *   .venv/bin/python -m weather_bot.main          # signal generator
0 2 * * *         /path/to/morning.sh                            # settlement + retrain
```

## Operational scripts

```bash
./tick.sh         # one signal-generation pass (called by cron)
./morning.sh      # nightly: settle fills, P&L report, retrain bias, verify
```

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
