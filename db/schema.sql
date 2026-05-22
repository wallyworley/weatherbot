-- PostgreSQL schema for the weather bot.
-- Recommended extension: TimescaleDB (CREATE EXTENSION timescaledb;) and convert
-- the *_forecast and metar_obs tables to hypertables keyed on (valid_time).

CREATE TABLE IF NOT EXISTS stations (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lat  DOUBLE PRECISION NOT NULL,
    lon  DOUBLE PRECISION NOT NULL,
    tz   TEXT NOT NULL
);

-- Deterministic forecasts (HRRR, NBM core).
CREATE TABLE IF NOT EXISTS det_forecast (
    station       TEXT NOT NULL REFERENCES stations(code),
    model         TEXT NOT NULL,            -- 'HRRR', 'NBM_CORE'
    run_time      TIMESTAMPTZ NOT NULL,     -- model cycle time (e.g., 2026-04-18 12:00Z)
    valid_time    TIMESTAMPTZ NOT NULL,     -- forecast valid time
    lead_hr       INT NOT NULL,
    var           TEXT NOT NULL,            -- 'TMP_2M'
    value         DOUBLE PRECISION NOT NULL, -- degrees F
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, model, run_time, valid_time, var)
);

-- Probabilistic NBM percentiles.
CREATE TABLE IF NOT EXISTS prob_forecast (
    station       TEXT NOT NULL REFERENCES stations(code),
    model         TEXT NOT NULL,            -- 'NBM_QMD'
    run_time      TIMESTAMPTZ NOT NULL,
    valid_date    DATE NOT NULL,            -- local date of the daily Tmax/Tmin
    var           TEXT NOT NULL,            -- 'TMAX_DAILY' | 'TMIN_DAILY'
    percentile    INT NOT NULL,             -- 1,5,10,25,50,75,90,95,99
    value         DOUBLE PRECISION NOT NULL, -- degrees F
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, model, run_time, valid_date, var, percentile)
);

-- True ensemble member forecasts (GEFS / ECMWF IFS / ECMWF AIFS via Open-Meteo).
-- Unlike det_forecast, each row preserves one perturbed member so research can
-- compute empirical bucket probabilities instead of fitting a normal curve to
-- point forecasts.
CREATE TABLE IF NOT EXISTS ensemble_forecast (
    station       TEXT NOT NULL REFERENCES stations(code),
    model         TEXT NOT NULL,             -- 'GFS_ENS', 'ECMWF_IFS_ENS', 'ECMWF_AIFS_ENS'
    run_time      TIMESTAMPTZ NOT NULL,
    valid_time    TIMESTAMPTZ NOT NULL,
    lead_hr       INT NOT NULL,
    var           TEXT NOT NULL,             -- 'TMP_2M'
    member        TEXT NOT NULL,             -- 'control', 'member01', ...
    value         DOUBLE PRECISION NOT NULL, -- degrees F
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, model, run_time, valid_time, var, member)
);

-- Surface observations (settlement + bias training data).
CREATE TABLE IF NOT EXISTS metar_obs (
    station    TEXT NOT NULL REFERENCES stations(code),
    obs_time   TIMESTAMPTZ NOT NULL,
    temp_f     DOUBLE PRECISION,
    dewpoint_f DOUBLE PRECISION,
    wind_kt    DOUBLE PRECISION,
    raw        TEXT,
    PRIMARY KEY (station, obs_time)
);

-- Daily settled observations (Tmax / Tmin per local calendar day).
CREATE TABLE IF NOT EXISTS daily_obs (
    station    TEXT NOT NULL REFERENCES stations(code),
    local_date DATE NOT NULL,
    tmax_f     DOUBLE PRECISION,
    tmin_f     DOUBLE PRECISION,
    source     TEXT NOT NULL DEFAULT 'METAR',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, local_date)
);

-- Upper-air + boundary-layer + radiation signals from Open-Meteo's GFS feed.
-- Used as features for fair-prob bias correction and reversal-risk scoring:
--   bl_height_m: deeper mixing layer = stronger surface heating → TMAX overshoot
--   tmp_850_f / tmp_925_f: warm/cold air advection aloft, classic forecaster signal
--   cloud_cover_pct: high cloud suppresses afternoon heating
--   solar_w_m2: realized incoming radiation
-- Pulled hourly via jobs/pull_atmos.py, ~48h ahead per station.
CREATE TABLE IF NOT EXISTS atmosphere_signals (
    station         TEXT NOT NULL REFERENCES stations(code),
    valid_time      TIMESTAMPTZ NOT NULL,    -- forecast valid time (UTC)
    run_time        TIMESTAMPTZ NOT NULL,    -- when we pulled it (proxy for cycle)
    bl_height_m     DOUBLE PRECISION,
    tmp_850_f       DOUBLE PRECISION,
    tmp_925_f       DOUBLE PRECISION,
    cloud_cover_pct DOUBLE PRECISION,
    solar_w_m2      DOUBLE PRECISION,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, run_time, valid_time)
);
CREATE INDEX IF NOT EXISTS idx_atmos_valid ON atmosphere_signals (station, valid_time DESC);

-- NWS Daily Climate Report (CLI) — Kalshi NHIGH settlement authority.
-- Forecaster-reviewed, more authoritative than METAR-derived daily extremes.
-- 30-day comparison vs METAR showed METAR undercounts CLI TMAX by 0.5-1°F
-- (12-22 of 30 days had >0.5°F gap). settle_paper_fills prefers cli_obs over
-- daily_obs when both exist for the same (station, local_date).
CREATE TABLE IF NOT EXISTS cli_obs (
    station        TEXT NOT NULL REFERENCES stations(code),
    local_date     DATE NOT NULL,                  -- date the report covers
    tmax_f         DOUBLE PRECISION,
    tmax_time_lst  TEXT,                           -- e.g. "400 PM" or "12:15 AM"
    tmin_f         DOUBLE PRECISION,
    tmin_time_lst  TEXT,
    section        TEXT,                           -- 'YESTERDAY' (final) | 'TODAY' (intraday)
    issued_at      TIMESTAMPTZ NOT NULL,           -- product issuance time
    raw_text       TEXT,                           -- full CLI text for audit / reparse
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, local_date)
);
CREATE INDEX IF NOT EXISTS idx_cli_local_date ON cli_obs (local_date DESC);

-- Rolling bias per station / month / lead-hour.
CREATE TABLE IF NOT EXISTS station_bias (
    station        TEXT NOT NULL REFERENCES stations(code),
    model          TEXT NOT NULL,
    var            TEXT NOT NULL,      -- 'TMAX_DAILY' | 'TMIN_DAILY'
    month          INT NOT NULL,       -- 1..12
    lead_day       INT NOT NULL,       -- 0 = today, 1 = tomorrow, etc.
    -- 2026-05-17: cycle_hour added so NBM 12Z (the worst-calibrated cycle per
    -- the PIT replay) gets its own bias cell. -1 = cycle-agnostic legacy/fallback
    -- row; 0/6/12/18 = NBM cycle-specific row. Retrain writes both lanes so
    -- lookups can prefer the cycle-specific cell when sample size allows and
    -- fall back to the agnostic cell otherwise.
    cycle_hour     SMALLINT NOT NULL DEFAULT -1,
    mean_bias_f    DOUBLE PRECISION NOT NULL,   -- forecast - obs
    stddev_f       DOUBLE PRECISION NOT NULL,
    sample_size    INT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, model, var, month, lead_day, cycle_hour)
);
-- Migration for pre-2026-05-17 databases:
ALTER TABLE station_bias ADD COLUMN IF NOT EXISTS cycle_hour SMALLINT NOT NULL DEFAULT -1;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'station_bias_pkey'
           AND pg_get_constraintdef(oid) NOT LIKE '%cycle_hour%'
    ) THEN
        ALTER TABLE station_bias DROP CONSTRAINT station_bias_pkey;
        ALTER TABLE station_bias ADD PRIMARY KEY (station, model, var, month, lead_day, cycle_hour);
    END IF;
END $$;

-- Kalshi markets and bucket structure.
CREATE TABLE IF NOT EXISTS kalshi_market (
    ticker         TEXT PRIMARY KEY,
    event_ticker   TEXT NOT NULL,
    station        TEXT REFERENCES stations(code),
    var            TEXT,               -- 'TMAX_DAILY' | 'TMIN_DAILY'
    valid_date     DATE,
    lower_f        DOUBLE PRECISION,   -- inclusive
    upper_f        DOUBLE PRECISION,   -- exclusive; NULL means open-ended
    status         TEXT,
    payload        JSONB,              -- raw market metadata
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Our fair-value estimates and generated signals.
CREATE TABLE IF NOT EXISTS signal (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker         TEXT NOT NULL REFERENCES kalshi_market(ticker),
    side           TEXT NOT NULL,      -- 'YES' | 'NO'
    fair_prob      DOUBLE PRECISION NOT NULL,
    market_ask     DOUBLE PRECISION,
    market_bid     DOUBLE PRECISION,
    edge           DOUBLE PRECISION NOT NULL,  -- after fees, per contract
    ev_per_dollar  DOUBLE PRECISION NOT NULL,
    kelly_fraction DOUBLE PRECISION NOT NULL,
    size_usd       DOUBLE PRECISION NOT NULL,
    action         TEXT NOT NULL,      -- 'OPEN' | 'HOLD' | 'REDUCE' | 'SKIP'
    notes          TEXT,
    skip_reason    TEXT                -- canonical reason when action='SKIP':
                                       -- DIVERGENCE | FEE_LOAD | NO_EDGE | NO_BOOK |
                                       -- TRIPWIRE_RED | BIAS_GATE | PROFIT_GATE
);
-- Backfill for existing DBs created before skip_reason was added:
ALTER TABLE signal ADD COLUMN IF NOT EXISTS skip_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_signal_skip_reason ON signal (skip_reason) WHERE skip_reason IS NOT NULL;
-- Multi-model directional votes per signal: {"NBM":"YES","HRRR":"YES","GFS":"NO","n_yes":2,"n_no":1}
ALTER TABLE signal ADD COLUMN IF NOT EXISTS model_votes JSONB;
-- Composite reversal-risk score + per-component breakdown (Sprint 3).
-- Shape: {"score":0.42, "label":"MEDIUM", "components": {...weighted contributions}}
-- Diagnostic-only initially — not used to gate or size trades until evaluated.
ALTER TABLE signal ADD COLUMN IF NOT EXISTS reversal_risk JSONB;

-- Paper-trade fills (so we can attribute PnL).
CREATE TABLE IF NOT EXISTS paper_fill (
    id          BIGSERIAL PRIMARY KEY,
    signal_id   BIGINT REFERENCES signal(id),
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker      TEXT NOT NULL REFERENCES kalshi_market(ticker),
    side        TEXT NOT NULL,
    price       DOUBLE PRECISION NOT NULL,
    contracts   INT NOT NULL,
    fees        DOUBLE PRECISION NOT NULL,
    settled     BOOLEAN NOT NULL DEFAULT FALSE,
    payout      DOUBLE PRECISION
);
-- Early exits close a paper fill before final settlement. Keep `payout` reserved
-- for final per-contract settlement outcome (1.0 win / 0.0 loss); exit proceeds
-- live in separate fields so calibration can ignore exited trades.
ALTER TABLE paper_fill ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION;
ALTER TABLE paper_fill ADD COLUMN IF NOT EXISTS exit_fees DOUBLE PRECISION DEFAULT 0.0;
ALTER TABLE paper_fill ADD COLUMN IF NOT EXISTS exit_ts TIMESTAMPTZ;
ALTER TABLE paper_fill ADD COLUMN IF NOT EXISTS exit_snapshot_ts TIMESTAMPTZ;
ALTER TABLE paper_fill ADD COLUMN IF NOT EXISTS exit_reason TEXT;
CREATE INDEX IF NOT EXISTS idx_paper_fill_exit ON paper_fill (exit_ts) WHERE exit_price IS NOT NULL;

-- Pending paper orders model a maker-first workflow instead of assuming every
-- qualifying signal immediately crosses the spread. The processor fills them
-- only when later snapshots show executable price and size before expiry.
CREATE TABLE IF NOT EXISTS paper_order (
    id               BIGSERIAL PRIMARY KEY,
    signal_id        BIGINT REFERENCES signal(id),
    ticker           TEXT NOT NULL REFERENCES kalshi_market(ticker),
    side             TEXT NOT NULL,
    limit_price      DOUBLE PRECISION NOT NULL,
    contracts        INT NOT NULL,
    fees_est         DOUBLE PRECISION NOT NULL,
    status           TEXT NOT NULL DEFAULT 'PENDING',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL,
    filled_at        TIMESTAMPTZ,
    fill_price       DOUBLE PRECISION,
    fill_snapshot_ts TIMESTAMPTZ,
    paper_fill_id    BIGINT REFERENCES paper_fill(id),
    notes            TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_order_pending ON paper_order (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_paper_order_ticker ON paper_order (ticker, created_at DESC);

-- Verification metrics snapshots (one row per nightly run).
CREATE TABLE IF NOT EXISTS verification (
    run_date    DATE NOT NULL,
    station     TEXT NOT NULL,
    var         TEXT NOT NULL,
    lead_day    INT NOT NULL,
    brier       DOUBLE PRECISION,
    crps        DOUBLE PRECISION,
    log_loss    DOUBLE PRECISION,
    reliability JSONB,
    n           INT NOT NULL,
    PRIMARY KEY (run_date, station, var, lead_day)
);

-- Orderbook snapshots — one row per market per pull cycle.
-- 60 days of snapshots gives a backtest dataset that cannot be reconstructed
-- from Kalshi's API retroactively.
CREATE TABLE IF NOT EXISTS market_snapshot (
    ticker        TEXT        NOT NULL REFERENCES kalshi_market(ticker),
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    yes_ask       NUMERIC,
    yes_bid       NUMERIC,
    yes_ask_size  INT,
    yes_bid_size  INT,
    -- NO-side captured separately because Kalshi's NO orderbook can have its
    -- own bid/ask that diverges from (1 - yes_*) on low-volume markets due
    -- to fee-aware spread asymmetry. Required for backtest fidelity when the
    -- bot trades the NO side.
    no_ask        NUMERIC,
    no_bid        NUMERIC,
    no_ask_size   INT,
    no_bid_size   INT,
    status        TEXT,
    last_price    NUMERIC,
    volume_24h    NUMERIC,
    open_interest NUMERIC,
    PRIMARY KEY (ticker, ts)
);
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS status TEXT;

CREATE INDEX IF NOT EXISTS idx_det_valid       ON det_forecast (station, valid_time);
CREATE INDEX IF NOT EXISTS idx_prob_valid      ON prob_forecast (station, valid_date);
CREATE INDEX IF NOT EXISTS idx_ensemble_valid  ON ensemble_forecast (station, model, valid_time);
CREATE INDEX IF NOT EXISTS idx_ensemble_run    ON ensemble_forecast (station, model, run_time DESC);
CREATE INDEX IF NOT EXISTS idx_metar_time      ON metar_obs (station, obs_time DESC);
CREATE INDEX IF NOT EXISTS idx_market_date     ON kalshi_market (station, valid_date);
CREATE INDEX IF NOT EXISTS idx_signal_ticker   ON signal (ticker, ts DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_ts     ON market_snapshot (ts);
-- Backfill for existing DBs created before NO-side capture was added:
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS no_ask      NUMERIC;
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS no_bid      NUMERIC;
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS no_ask_size INT;
ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS no_bid_size INT;

-- External prediction-market snapshots. Initial use is read-only Polymarket
-- weather buckets, kept separate from Kalshi so cross-platform gap research
-- cannot alter trading-path data.
CREATE TABLE IF NOT EXISTS external_market_snapshot (
    venue             TEXT NOT NULL,             -- 'POLYMARKET'
    event_slug        TEXT NOT NULL,
    market_slug       TEXT NOT NULL,
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
    question          TEXT NOT NULL,
    station           TEXT,
    valid_date        DATE,
    lower_f           DOUBLE PRECISION,
    upper_f           DOUBLE PRECISION,
    resolution_source TEXT,
    yes_token_id      TEXT,
    no_token_id       TEXT,
    yes_bid           NUMERIC,
    yes_ask           NUMERIC,
    yes_ask_size      NUMERIC,
    no_bid            NUMERIC,
    no_ask            NUMERIC,
    no_ask_size       NUMERIC,
    volume_24h        NUMERIC,
    liquidity         NUMERIC,
    payload           JSONB,
    PRIMARY KEY (venue, market_slug, ts)
);
CREATE INDEX IF NOT EXISTS idx_external_snapshot_ts ON external_market_snapshot (venue, ts DESC);
CREATE INDEX IF NOT EXISTS idx_external_snapshot_station ON external_market_snapshot (venue, station, valid_date, ts DESC);

-- ---------------------------------------------------------------------------
-- Health & autonomy tables
-- ---------------------------------------------------------------------------

-- Hourly health-check snapshots. The dashboard reads from here; the trade
-- loop reads from here to short-circuit when status is RED.
CREATE TABLE IF NOT EXISTS health_check (
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    station             TEXT NOT NULL,                 -- 'GLOBAL' for system-wide
    component           TEXT NOT NULL,                 -- 'DATA' | 'MODEL' | 'MARKETS' | 'RISK' | 'PNL'
    status              TEXT NOT NULL,                 -- 'GREEN' | 'AMBER' | 'RED'
    metric_value        DOUBLE PRECISION,              -- the metric that drove status (Brier, edge_diff, lag_min, etc.)
    threshold_amber     DOUBLE PRECISION,
    threshold_red       DOUBLE PRECISION,
    detail              JSONB,                         -- structured detail for the dashboard
    acknowledged_at     TIMESTAMPTZ,
    acknowledged_by     TEXT,
    alerted_at          TIMESTAMPTZ,                   -- set once macOS notification (and optional iMessage) was sent
    PRIMARY KEY (ts, station, component)
);

CREATE INDEX IF NOT EXISTS idx_health_recent ON health_check (ts DESC);
CREATE INDEX IF NOT EXISTS idx_health_status ON health_check (status, ts DESC) WHERE status IN ('AMBER', 'RED');

-- Latest-status view. The trade loop reads this to decide whether to skip.
CREATE OR REPLACE VIEW health_check_latest AS
SELECT DISTINCT ON (station, component)
       station, component, ts, status, metric_value, detail, acknowledged_at, alerted_at
  FROM health_check
 ORDER BY station, component, ts DESC;

-- Snapshot of station_bias for drift detection. One row per (snapshot_date,
-- station, model, var, month, lead_day). The drift detector compares
-- yesterday's snapshot to today's bias and flags >2σ moves.
CREATE TABLE IF NOT EXISTS station_bias_history (
    snapshot_date  DATE NOT NULL,
    station        TEXT NOT NULL,
    model          TEXT NOT NULL,
    var            TEXT NOT NULL,
    month          INT NOT NULL,
    lead_day       INT NOT NULL,
    cycle_hour     SMALLINT NOT NULL DEFAULT -1,
    mean_bias_f    DOUBLE PRECISION NOT NULL,
    stddev_f       DOUBLE PRECISION NOT NULL,
    sample_size    INT NOT NULL,
    PRIMARY KEY (snapshot_date, station, model, var, month, lead_day, cycle_hour)
);
-- Migration for pre-2026-05-17 databases:
ALTER TABLE station_bias_history ADD COLUMN IF NOT EXISTS cycle_hour SMALLINT NOT NULL DEFAULT -1;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'station_bias_history_pkey'
           AND pg_get_constraintdef(oid) NOT LIKE '%cycle_hour%'
    ) THEN
        ALTER TABLE station_bias_history DROP CONSTRAINT station_bias_history_pkey;
        ALTER TABLE station_bias_history ADD PRIMARY KEY
            (snapshot_date, station, model, var, month, lead_day, cycle_hour);
    END IF;
END $$;

-- Bias drift events — one row per detected anomaly. Dashboard reads these
-- to surface "did our fetcher break again?" type incidents.
CREATE TABLE IF NOT EXISTS bias_drift_event (
    id              BIGSERIAL PRIMARY KEY,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    station         TEXT NOT NULL,
    model           TEXT NOT NULL,
    var             TEXT NOT NULL,
    month           INT NOT NULL,
    lead_day        INT NOT NULL,
    prev_mean       DOUBLE PRECISION NOT NULL,
    new_mean        DOUBLE PRECISION NOT NULL,
    delta_sigma     DOUBLE PRECISION NOT NULL,         -- how many σ the move was
    sample_size     INT NOT NULL,
    severity        TEXT NOT NULL,                     -- 'WATCH' | 'ALERT'
    acknowledged_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_drift_recent ON bias_drift_event (detected_at DESC);
