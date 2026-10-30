-- Periodic device state observations are not transition events.
CREATE TABLE IF NOT EXISTS device_state_snapshots (
    device_id VARCHAR(64) NOT NULL REFERENCES devices(device_id),
    boot_id VARCHAR(64) NOT NULL,
    seq BIGINT NOT NULL CHECK (seq >= 0),
    measured_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    received_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    ts_source VARCHAR(16) NOT NULL CHECK (ts_source IN ('device','server')),
    state_json JSONB NOT NULL,
    payload_json JSONB NOT NULL,
    PRIMARY KEY (device_id, boot_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_device_state_snapshots_latest
    ON device_state_snapshots(device_id, measured_at DESC, seq DESC);
