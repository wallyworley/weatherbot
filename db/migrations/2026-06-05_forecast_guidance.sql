-- Migration: official / station-specific guidance research lane.
-- Safe to re-run.
BEGIN;

CREATE TABLE IF NOT EXISTS forecast_guidance (
    station       TEXT NOT NULL REFERENCES stations(code),
    source        TEXT NOT NULL,
    run_time      TIMESTAMPTZ NOT NULL,
    valid_time    TIMESTAMPTZ NOT NULL,
    valid_date    DATE NOT NULL,
    lead_hr       INT,
    var           TEXT NOT NULL,
    value         DOUBLE PRECISION NOT NULL,
    units         TEXT NOT NULL DEFAULT 'degF',
    raw           JSONB,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station, source, run_time, valid_time, var)
);

CREATE INDEX IF NOT EXISTS idx_guidance_valid
    ON forecast_guidance (station, source, valid_date, var, run_time DESC);

CREATE INDEX IF NOT EXISTS idx_guidance_ingested
    ON forecast_guidance (source, ingested_at DESC);

COMMIT;
