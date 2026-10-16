-- Additive M1 Rule Engine migration. Safe to run repeatedly on an existing volume.
ALTER TABLE rule_definitions
    ADD COLUMN IF NOT EXISTS clear_hold_seconds INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS business_start TIME WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS business_end TIME WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS schedule_timezone VARCHAR(64);

ALTER TABLE rule_runtime_state
    ADD COLUMN IF NOT EXISTS recovery_started_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS episode_key VARCHAR(160),
    ADD COLUMN IF NOT EXISTS episode_started_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS current_state VARCHAR(16),
    ADD COLUMN IF NOT EXISTS state_changed_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS source_event_id VARCHAR(160);

ALTER TABLE alarm_occurrences
    ADD COLUMN IF NOT EXISTS episode_key VARCHAR(160),
    ADD COLUMN IF NOT EXISTS occurrence_kind VARCHAR(12) NOT NULL DEFAULT 'TRIGGERED',
    ADD COLUMN IF NOT EXISTS cleared_at TIMESTAMP WITHOUT TIME ZONE;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_alarm_occurrence_kind') THEN
        ALTER TABLE alarm_occurrences ADD CONSTRAINT ck_alarm_occurrence_kind
            CHECK (occurrence_kind IN ('TRIGGERED', 'CLEARED'));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_alarm_episode_kind
    ON alarm_occurrences (episode_key, occurrence_kind) WHERE episode_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rule_runtime_due
    ON rule_runtime_state (current_state, state_changed_at) WHERE current_state = 'open';

UPDATE rule_definitions SET hold_seconds=300, hysteresis_value=1.0,
    clear_hold_seconds=120, quiet_seconds=600,
    updated_at=CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
WHERE rule_code='S1_TEMP_HIGH' AND hold_seconds=30 AND quiet_seconds=30;

UPDATE rule_definitions SET hold_seconds=300, clear_hold_seconds=0, quiet_seconds=600,
    business_start=TIME '08:00:00', business_end=TIME '18:00:00',
    schedule_timezone='Asia/Ho_Chi_Minh', updated_at=CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
WHERE rule_code='S6_DOOR_OPEN'
  AND business_start IS NULL AND business_end IS NULL AND schedule_timezone IS NULL;
