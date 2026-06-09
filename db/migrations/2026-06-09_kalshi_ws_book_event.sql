-- Migration: research-only high-cadence Kalshi WebSocket book events (EXP-2026-011, amendment A5).
-- Tightens onset timing against polling censoring. NOT read by production probabilities, sizing,
-- execution, gates, or station activation. Safe to re-run.
BEGIN;

CREATE TABLE IF NOT EXISTS kalshi_ws_book_event (
    id           BIGSERIAL PRIMARY KEY,
    ticker       TEXT NOT NULL,
    msg_type     TEXT NOT NULL,        -- orderbook_snapshot | orderbook_delta
    seq          BIGINT,               -- per-subscription sequence, if present
    exchange_ts  TIMESTAMPTZ,          -- exchange ts/ts_ms, if present
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- our receipt time (single VPS clock)
    yes_bid      NUMERIC,              -- derived best yes buy (cents)
    yes_ask      NUMERIC,              -- derived 100 - best no buy (cents)
    payload      JSONB,                -- raw message msg body (for re-derivation)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kalshi_ws_book_ticker_recv
    ON kalshi_ws_book_event (ticker, received_at DESC);

COMMIT;
