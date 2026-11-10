-- Freeze Rule provenance per episode and retain immutable nextGRP mapping versions.
-- Additive and idempotent. Historical Alarm/Outbox payloads are never rewritten.
CREATE TABLE IF NOT EXISTS integration_identity_mapping_versions (
    system_code VARCHAR(32) NOT NULL,
    entity_type VARCHAR(16) NOT NULL,
    internal_id VARCHAR(128) NOT NULL,
    external_id VARCHAR(128) NOT NULL,
    config_version BIGINT NOT NULL CHECK (config_version >= 0),
    identity_hash VARCHAR(64) NOT NULL,
    confirmed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    PRIMARY KEY (system_code, entity_type, internal_id, config_version),
    UNIQUE (system_code, entity_type, external_id, config_version)
);

INSERT INTO integration_identity_mapping_versions
    (system_code,entity_type,internal_id,external_id,config_version,identity_hash)
SELECT system_code,entity_type,internal_id,external_id,config_version,
       md5(system_code || '|' || entity_type || '|' || internal_id || '|' ||
           external_id || '|' || config_version::text)
FROM integration_identity_mappings
WHERE confirmed=TRUE AND enabled=TRUE
ON CONFLICT DO NOTHING;

ALTER TABLE rule_runtime_state
    ADD COLUMN IF NOT EXISTS frozen_rule_version BIGINT,
    ADD COLUMN IF NOT EXISTS frozen_rule_payload_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS frozen_rule_config_json JSONB,
    ADD COLUMN IF NOT EXISTS frozen_identity_json JSONB,
    ADD COLUMN IF NOT EXISTS episode_provenance VARCHAR(16);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname='ck_rule_runtime_episode_provenance') THEN
        ALTER TABLE rule_runtime_state ADD CONSTRAINT ck_rule_runtime_episode_provenance
            CHECK (episode_provenance IS NULL OR episode_provenance IN ('FROZEN','LEGACY'));
    END IF;
END $$;

-- Existing in-flight episodes have no trustworthy creation-time Rule snapshot.
-- Mark them explicitly rather than guessing a version from the current registry.
UPDATE rule_runtime_state
SET episode_provenance='LEGACY'
WHERE episode_provenance IS NULL
  AND (episode_key IS NOT NULL OR hold_started_at IS NOT NULL OR is_active=TRUE
       OR recovery_started_at IS NOT NULL OR current_state='open');

ALTER TABLE alarm_episode_mappings
    ADD COLUMN IF NOT EXISTS frozen_rule_version BIGINT,
    ADD COLUMN IF NOT EXISTS frozen_rule_payload_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS frozen_identity_json JSONB,
    ADD COLUMN IF NOT EXISTS episode_provenance VARCHAR(16);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname='ck_alarm_episode_provenance') THEN
        ALTER TABLE alarm_episode_mappings ADD CONSTRAINT ck_alarm_episode_provenance
            CHECK (episode_provenance IS NULL OR episode_provenance IN ('FROZEN','LEGACY'));
    END IF;
END $$;

UPDATE alarm_episode_mappings
SET episode_provenance='LEGACY'
WHERE episode_provenance IS NULL;

CREATE INDEX IF NOT EXISTS idx_mapping_version_lookup
    ON integration_identity_mapping_versions
       (system_code,entity_type,internal_id,config_version);

