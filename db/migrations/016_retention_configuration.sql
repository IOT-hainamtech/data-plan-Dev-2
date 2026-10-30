-- U-TEL-02: configurable retention control. This migration is additive and
-- deliberately does NOT enable data deletion on an existing/runtime database.
CREATE TABLE IF NOT EXISTS telemetry_retention_config (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    retention_days INTEGER NOT NULL DEFAULT 90 CHECK (retention_days BETWEEN 1 AND 3650),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    changed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    changed_by VARCHAR(160) NOT NULL DEFAULT 'migration'
);

INSERT INTO telemetry_retention_config(singleton,retention_days,enabled)
VALUES(TRUE,90,FALSE) ON CONFLICT(singleton) DO NOTHING;

CREATE OR REPLACE FUNCTION enable_telemetry_retention(
    requested_days INTEGER DEFAULT 90, actor VARCHAR DEFAULT 'operator')
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF requested_days < 1 OR requested_days > 3650 THEN
        RAISE EXCEPTION 'retention days out of range';
    END IF;
    PERFORM add_retention_policy('telemetry', make_interval(days => requested_days),
                                 if_not_exists => TRUE);
    UPDATE telemetry_retention_config SET retention_days=requested_days,enabled=TRUE,
        changed_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),changed_by=actor
    WHERE singleton=TRUE;
END $$;

CREATE OR REPLACE FUNCTION disable_telemetry_retention(actor VARCHAR DEFAULT 'operator')
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    PERFORM remove_retention_policy('telemetry', if_exists => TRUE);
    UPDATE telemetry_retention_config SET enabled=FALSE,
        changed_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),changed_by=actor
    WHERE singleton=TRUE;
END $$;
