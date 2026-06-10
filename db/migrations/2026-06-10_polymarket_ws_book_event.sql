-- EXP-2026-011 amendment A7 (2026-06-10): research-only Polymarket WebSocket book events.
-- Mirrors kalshi_ws_book_event (A5) for the cross-venue channel's PM-side timing.
-- NOT read by any production probability/sizing/execution/gate path.
-- NOTE: bid/ask here are DOLLARS (0..1) per Polymarket CLOB convention
-- (kalshi_ws_book_event stores integer cents).

CREATE TABLE IF NOT EXISTS polymarket_ws_book_event (
    id          bigserial PRIMARY KEY,
    asset_id    text NOT NULL,
    market_slug text,
    station     text,
    valid_date  date,
    lower_f     double precision,
    upper_f     double precision,
    msg_type    text NOT NULL,
    exchange_ts timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    bid         numeric,
    ask         numeric,
    payload     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pm_ws_book_station_date
    ON polymarket_ws_book_event (station, valid_date, received_at);
CREATE INDEX IF NOT EXISTS idx_pm_ws_book_asset_recv
    ON polymarket_ws_book_event (asset_id, received_at DESC);
