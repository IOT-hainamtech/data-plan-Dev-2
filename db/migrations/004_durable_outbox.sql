-- Durable Outbox Worker migration. Additive and safe for existing alarm rows.
ALTER TABLE alarm_outbox DROP CONSTRAINT IF EXISTS alarm_outbox_delivery_state_check;
ALTER TABLE alarm_outbox
    ADD COLUMN IF NOT EXISTS failure_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_by VARCHAR(128),
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS ack_id VARCHAR(160),
    ADD COLUMN IF NOT EXISTS acked_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS last_http_status INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_outbox_delivery_state') THEN
        ALTER TABLE alarm_outbox ADD CONSTRAINT ck_outbox_delivery_state
            CHECK (delivery_state IN ('PENDING','IN_FLIGHT','DELIVERED','FAILED'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_outbox_failure_count') THEN
        ALTER TABLE alarm_outbox ADD CONSTRAINT ck_outbox_failure_count CHECK (failure_count >= 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_outbox_claim
    ON alarm_outbox (next_attempt_at, id)
    WHERE delivery_state IN ('PENDING','IN_FLIGHT');
