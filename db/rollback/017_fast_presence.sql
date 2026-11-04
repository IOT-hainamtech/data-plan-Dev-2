DROP INDEX IF EXISTS idx_device_status_fast_presence;
ALTER TABLE device_status
    DROP COLUMN IF EXISTS presence_received_at,
    DROP COLUMN IF EXISTS presence_seq,
    DROP COLUMN IF EXISTS presence_boot_id;
