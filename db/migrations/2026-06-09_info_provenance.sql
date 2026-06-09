-- Migration: research-only first-seen provenance for EXP-2026-011.
-- Safe to re-run.
BEGIN;

CREATE TABLE IF NOT EXISTS info_provenance (
    id             BIGSERIAL PRIMARY KEY,
    source_type    TEXT NOT NULL,
    station        TEXT,
    official_ts    TIMESTAMPTZ,
    event_key      TEXT NOT NULL,
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    value_summary  JSONB,
    ingest_host    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_type, event_key)
);

CREATE INDEX IF NOT EXISTS idx_info_provenance_seen
    ON info_provenance (source_type, first_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_info_provenance_station_seen
    ON info_provenance (station, source_type, first_seen_at DESC);

COMMIT;
