-- Fast Presence is opt-in through FAST_PRESENCE_DEVICE_IDS. These nullable
-- fields only provide durable ordering and timing; this migration enables no device.
ALTER TABLE device_status
    ADD COLUMN IF NOT EXISTS presence_boot_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS presence_seq BIGINT,
    ADD COLUMN IF NOT EXISTS presence_received_at TIMESTAMP WITHOUT TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_device_status_fast_presence
    ON device_status (presence_received_at)
    WHERE current_state = 'online' AND presence_received_at IS NOT NULL;
