-- EXP-2026-015: venue-wide settled-market census + calibration reference prices.
-- Research-only; never read by any production probability/sizing/execution/gate path.

CREATE TABLE IF NOT EXISTS kalshi_settled_market (
    ticker          text PRIMARY KEY,
    event_ticker    text,
    series_ticker   text,
    category        text,
    title           text,
    open_time       timestamptz,
    close_time      timestamptz,
    result          text,
    volume_fp       numeric,
    liquidity_dollars numeric,
    last_price_dollars numeric,
    strike_type     text,
    ref_day         date,
    ref_yes_bid     numeric,
    ref_yes_ask     numeric,
    ref_status      text,          -- pending | ok | no_candle | short_life | low_volume
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ksm_close ON kalshi_settled_market (close_time);
CREATE INDEX IF NOT EXISTS idx_ksm_ref_status ON kalshi_settled_market (ref_status);
CREATE INDEX IF NOT EXISTS idx_ksm_category ON kalshi_settled_market (category);
