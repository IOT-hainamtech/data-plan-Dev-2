-- Safe rollback for migration 019. Refuse to discard provenance already used by
-- a new episode; use a forward fix after production acceptance instead.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM rule_runtime_state WHERE episode_provenance='FROZEN'
               AND (episode_key IS NOT NULL OR hold_started_at IS NOT NULL OR is_active=TRUE
                    OR recovery_started_at IS NOT NULL OR current_state='open'))
       OR EXISTS (SELECT 1 FROM alarm_episode_mappings WHERE episode_provenance='FROZEN') THEN
        RAISE EXCEPTION 'rollback 019 refused: frozen episode provenance exists';
    END IF;
END $$;

ALTER TABLE alarm_episode_mappings
    DROP CONSTRAINT IF EXISTS ck_alarm_episode_provenance,
    DROP COLUMN IF EXISTS frozen_rule_version,
    DROP COLUMN IF EXISTS frozen_rule_payload_hash,
    DROP COLUMN IF EXISTS frozen_identity_json,
    DROP COLUMN IF EXISTS episode_provenance;

ALTER TABLE rule_runtime_state
    DROP CONSTRAINT IF EXISTS ck_rule_runtime_episode_provenance,
    DROP COLUMN IF EXISTS frozen_rule_version,
    DROP COLUMN IF EXISTS frozen_rule_payload_hash,
    DROP COLUMN IF EXISTS frozen_rule_config_json,
    DROP COLUMN IF EXISTS frozen_identity_json,
    DROP COLUMN IF EXISTS episode_provenance;

DROP TABLE IF EXISTS integration_identity_mapping_versions;
