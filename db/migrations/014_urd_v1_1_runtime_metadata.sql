-- Additive metadata required by URD W-TS and Status/Event traceability.
ALTER TABLE telemetry_reports
    ADD COLUMN IF NOT EXISTS ts_source VARCHAR(12) NOT NULL DEFAULT 'server',
    ADD COLUMN IF NOT EXISTS ts_suspect BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE telemetry
    ADD COLUMN IF NOT EXISTS ts_suspect BOOLEAN NOT NULL DEFAULT FALSE;
-- Preserve the legacy `device` value already stored by Dev 2 while accepting
-- the URD v1.1 vocabulary for all new rows.
ALTER TABLE telemetry DROP CONSTRAINT IF EXISTS telemetry_ts_source_check;
ALTER TABLE telemetry ADD CONSTRAINT telemetry_ts_source_check
    CHECK (ts_source IN ('ntp', 'none', 'server', 'device'));
ALTER TABLE device_state_snapshots
    ADD COLUMN IF NOT EXISTS ts_suspect BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE device_state_snapshots
    DROP CONSTRAINT IF EXISTS device_state_snapshots_ts_source_check;
ALTER TABLE device_state_snapshots
    ADD CONSTRAINT device_state_snapshots_ts_source_check
    CHECK (ts_source IN ('ntp', 'none', 'server', 'device'));
ALTER TABLE device_events
    ADD COLUMN IF NOT EXISTS ts_source VARCHAR(12) NOT NULL DEFAULT 'server',
    ADD COLUMN IF NOT EXISTS ts_suspect BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS event_source VARCHAR(12) NOT NULL DEFAULT 'device';
ALTER TABLE device_status_history
    ADD COLUMN IF NOT EXISTS previous_state VARCHAR(8),
    ADD COLUMN IF NOT EXISTS reason VARCHAR(16);
