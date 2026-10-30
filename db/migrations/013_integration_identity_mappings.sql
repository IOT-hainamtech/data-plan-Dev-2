-- Confirmed identity translation between the Dev 2 runtime and external control planes.
-- No mapping is seeded: an operator must explicitly confirm both sides first.
CREATE TABLE IF NOT EXISTS integration_identity_mappings (
    system_code VARCHAR(32) NOT NULL,
    entity_type VARCHAR(16) NOT NULL
        CHECK (entity_type IN ('ORGANIZATION','SITE','ZONE','PROFILE','DEVICE','RULE')),
    internal_id VARCHAR(128) NOT NULL,
    external_id VARCHAR(128) NOT NULL,
    config_version BIGINT NOT NULL DEFAULT 0 CHECK (config_version >= 0),
    confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    PRIMARY KEY (system_code, entity_type, internal_id),
    UNIQUE (system_code, entity_type, external_id)
);

CREATE INDEX IF NOT EXISTS idx_integration_identity_external
    ON integration_identity_mappings(system_code, entity_type, external_id)
    WHERE enabled AND confirmed;
