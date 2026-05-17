-- Migration: add cycle_hour to station_bias and station_bias_history.
-- Safe to re-run.
BEGIN;

ALTER TABLE station_bias
    ADD COLUMN IF NOT EXISTS cycle_hour SMALLINT NOT NULL DEFAULT -1;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'station_bias_pkey'
           AND pg_get_constraintdef(oid) NOT LIKE '%cycle_hour%'
    ) THEN
        ALTER TABLE station_bias DROP CONSTRAINT station_bias_pkey;
        ALTER TABLE station_bias
            ADD PRIMARY KEY (station, model, var, month, lead_day, cycle_hour);
    END IF;
END $$;

ALTER TABLE station_bias_history
    ADD COLUMN IF NOT EXISTS cycle_hour SMALLINT NOT NULL DEFAULT -1;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'station_bias_history_pkey'
           AND pg_get_constraintdef(oid) NOT LIKE '%cycle_hour%'
    ) THEN
        ALTER TABLE station_bias_history DROP CONSTRAINT station_bias_history_pkey;
        ALTER TABLE station_bias_history
            ADD PRIMARY KEY (snapshot_date, station, model, var, month, lead_day, cycle_hour);
    END IF;
END $$;

COMMIT;
