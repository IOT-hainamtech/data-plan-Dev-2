-- Additive migration 007: Device status timeout tracking and multi-device TH-002 support.
-- Safe to execute repeatedly on an existing volume.

-- 1. Extend device_status with last_seen_at and timeout_seconds
ALTER TABLE device_status
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS timeout_seconds INTEGER NOT NULL DEFAULT 120;

-- Backfill last_seen_at for existing rows
UPDATE device_status
SET last_seen_at = received_at
WHERE last_seen_at IS NULL;

-- 2. Index for efficient timeout reconciliation
CREATE INDEX IF NOT EXISTS idx_device_status_timeout
    ON device_status (current_state, last_seen_at)
    WHERE current_state = 'online';

-- 3. Register TH-002 with same TEMP_HUM_SENSOR profile as TH-001
INSERT INTO devices (device_id, org_code, site_code, profile_code, enabled)
VALUES ('TH-002', 'demo', 'demo-site', 'TEMP_HUM_SENSOR', TRUE)
ON CONFLICT (device_id) DO NOTHING;

-- 4. Default threshold rule for TH-002 (matching S1 specification)
INSERT INTO rule_definitions
(rule_code, device_id, rule_kind, metric, threshold_value,
 hysteresis_value, hold_seconds, clear_hold_seconds, quiet_seconds, severity, enabled)
VALUES
('S1_TH002_TEMP_HIGH', 'TH-002', 'THRESHOLD', 'temperature', 30.0,
 1.0, 300, 120, 600, 'HIGH', TRUE)
ON CONFLICT (rule_code) DO NOTHING;
