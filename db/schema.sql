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

-- Rolling bias per station / month / lead-hour.
CREATE TABLE IF NOT EXISTS station_bias (
    station        TEXT NOT NULL REFERENCES stations(code),
    model          TEXT NOT NULL,
    var            TEXT NOT NULL,      -- 'TMAX_DAILY' | 'TMIN_DAILY'
    month          INT NOT NULL,       -- 1..12
    lead_day       INT NOT NULL,       -- 0 = today, 1 = tomorrow, etc.
    mean_bias_f    DOUBLE PRECISION NOT NULL,   -- forecast - obs
    stddev_f       DOUBLE PRECISION NOT NULL,
    sample_size    INT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, model, var, month, lead_day)
);

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
    notes          TEXT
);

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
    last_price    NUMERIC,
    volume_24h    NUMERIC,
    open_interest NUMERIC,
    PRIMARY KEY (ticker, ts)
);

CREATE INDEX IF NOT EXISTS idx_det_valid       ON det_forecast (station, valid_time);
CREATE INDEX IF NOT EXISTS idx_prob_valid      ON prob_forecast (station, valid_date);
CREATE INDEX IF NOT EXISTS idx_metar_time      ON metar_obs (station, obs_time DESC);
CREATE INDEX IF NOT EXISTS idx_market_date     ON kalshi_market (station, valid_date);
CREATE INDEX IF NOT EXISTS idx_signal_ticker   ON signal (ticker, ts DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_ts     ON market_snapshot (ts);

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
    PRIMARY KEY (ts, station, component)
);

CREATE INDEX IF NOT EXISTS idx_health_recent ON health_check (ts DESC);
CREATE INDEX IF NOT EXISTS idx_health_status ON health_check (status, ts DESC) WHERE status IN ('AMBER', 'RED');

-- Latest-status view. The trade loop reads this to decide whether to skip.
CREATE OR REPLACE VIEW health_check_latest AS
SELECT DISTINCT ON (station, component)
       station, component, ts, status, metric_value, detail, acknowledged_at
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
    mean_bias_f    DOUBLE PRECISION NOT NULL,
    stddev_f       DOUBLE PRECISION NOT NULL,
    sample_size    INT NOT NULL,
    PRIMARY KEY (snapshot_date, station, model, var, month, lead_day)
);

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
