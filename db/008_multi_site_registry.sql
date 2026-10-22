-- Additive multi-organization/site/zone registry and profile-driven contracts.
CREATE TABLE IF NOT EXISTS organizations (
    org_code VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(160) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS sites (
    org_code VARCHAR(64) NOT NULL REFERENCES organizations(org_code),
    site_code VARCHAR(64) NOT NULL,
    display_name VARCHAR(160) NOT NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_code, site_code)
);

CREATE TABLE IF NOT EXISTS zones (
    org_code VARCHAR(64) NOT NULL,
    site_code VARCHAR(64) NOT NULL,
    zone_code VARCHAR(64) NOT NULL,
    display_name VARCHAR(160) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (org_code, site_code, zone_code),
    FOREIGN KEY (org_code, site_code) REFERENCES sites(org_code, site_code)
);

CREATE TABLE IF NOT EXISTS device_profiles (
    profile_code VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(160) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS device_profile_points (
    profile_code VARCHAR(64) NOT NULL REFERENCES device_profiles(profile_code),
    channel VARCHAR(16) NOT NULL CHECK (channel IN ('telemetry', 'status')),
    point_code VARCHAR(64) NOT NULL,
    data_type VARCHAR(16) NOT NULL CHECK (data_type IN ('number', 'string', 'boolean')),
    unit VARCHAR(32),
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    allowed_values JSONB,
    required BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (profile_code, channel, point_code),
    CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value)
);

CREATE TABLE IF NOT EXISTS device_profile_events (
    profile_code VARCHAR(64) NOT NULL REFERENCES device_profiles(profile_code),
    event_type VARCHAR(80) NOT NULL,
    state_point VARCHAR(64),
    state_value VARCHAR(80),
    PRIMARY KEY (profile_code, event_type)
);

INSERT INTO organizations (org_code, display_name) VALUES ('demo', 'Demo Organization')
ON CONFLICT (org_code) DO NOTHING;
INSERT INTO sites (org_code, site_code, display_name, timezone)
VALUES ('demo', 'demo-site', 'Demo Site', 'Asia/Ho_Chi_Minh')
ON CONFLICT (org_code, site_code) DO NOTHING;
INSERT INTO zones (org_code, site_code, zone_code, display_name) VALUES
('demo', 'demo-site', 'office', 'Office'),
('demo', 'demo-site', 'entrance', 'Entrance')
ON CONFLICT (org_code, site_code, zone_code) DO NOTHING;

INSERT INTO device_profiles (profile_code, display_name) VALUES
('TEMP_HUM_SENSOR', 'Temperature and humidity sensor'),
('DOOR_SENSOR', 'Door contact sensor')
ON CONFLICT (profile_code) DO NOTHING;
INSERT INTO device_profile_points
    (profile_code, channel, point_code, data_type, unit, min_value, max_value, allowed_values, required)
VALUES
('TEMP_HUM_SENSOR', 'telemetry', 'temperature', 'number', 'Cel', -20, 80, NULL, FALSE),
('TEMP_HUM_SENSOR', 'telemetry', 'humidity', 'number', '%RH', 0, 100, NULL, FALSE),
('DOOR_SENSOR', 'status', 'door_state', 'string', NULL, NULL, NULL, '["open","closed"]'::jsonb, FALSE)
ON CONFLICT (profile_code, channel, point_code) DO NOTHING;
INSERT INTO device_profile_events (profile_code, event_type, state_point, state_value) VALUES
('DOOR_SENSOR', 'DOOR_OPENED', 'door_state', 'open'),
('DOOR_SENSOR', 'DOOR_CLOSED', 'door_state', 'closed')
ON CONFLICT (profile_code, event_type) DO NOTHING;

ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS zone_code VARCHAR(64),
    ADD COLUMN IF NOT EXISTS telemetry_interval_seconds INTEGER NOT NULL DEFAULT 60,
    ADD COLUMN IF NOT EXISTS heartbeat_interval_seconds INTEGER NOT NULL DEFAULT 60,
    ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;
UPDATE devices SET zone_code=CASE WHEN profile_code='DOOR_SENSOR' THEN 'entrance' ELSE 'office' END
WHERE org_code='demo' AND site_code='demo-site' AND zone_code IS NULL;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_devices_site') THEN
        ALTER TABLE devices ADD CONSTRAINT fk_devices_site
            FOREIGN KEY (org_code, site_code) REFERENCES sites(org_code, site_code);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_devices_zone') THEN
        ALTER TABLE devices ADD CONSTRAINT fk_devices_zone
            FOREIGN KEY (org_code, site_code, zone_code) REFERENCES zones(org_code, site_code, zone_code);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_devices_profile') THEN
        ALTER TABLE devices ADD CONSTRAINT fk_devices_profile
            FOREIGN KEY (profile_code) REFERENCES device_profiles(profile_code);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_devices_intervals') THEN
        ALTER TABLE devices ADD CONSTRAINT ck_devices_intervals CHECK
            (telemetry_interval_seconds > 0 AND heartbeat_interval_seconds > 0);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_devices_location ON devices(org_code, site_code, zone_code);

ALTER TABLE rule_definitions ALTER COLUMN device_id DROP NOT NULL;
ALTER TABLE rule_definitions
    ADD COLUMN IF NOT EXISTS scope_type VARCHAR(16) NOT NULL DEFAULT 'DEVICE',
    ADD COLUMN IF NOT EXISTS scope_org_code VARCHAR(64),
    ADD COLUMN IF NOT EXISTS scope_site_code VARCHAR(64),
    ADD COLUMN IF NOT EXISTS scope_zone_code VARCHAR(64),
    ADD COLUMN IF NOT EXISTS scope_profile_code VARCHAR(64);
UPDATE rule_definitions SET scope_type='DEVICE' WHERE scope_type IS NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_rule_scope_type') THEN
        ALTER TABLE rule_definitions ADD CONSTRAINT ck_rule_scope_type
            CHECK (scope_type IN ('DEVICE','PROFILE','ZONE','SITE','ORGANIZATION'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_rule_scope_shape') THEN
        ALTER TABLE rule_definitions ADD CONSTRAINT ck_rule_scope_shape CHECK (
            (scope_type='DEVICE' AND device_id IS NOT NULL) OR
            (scope_type='PROFILE' AND device_id IS NULL AND scope_profile_code IS NOT NULL) OR
            (scope_type='ZONE' AND device_id IS NULL AND scope_org_code IS NOT NULL
                AND scope_site_code IS NOT NULL AND scope_zone_code IS NOT NULL) OR
            (scope_type='SITE' AND device_id IS NULL AND scope_org_code IS NOT NULL
                AND scope_site_code IS NOT NULL) OR
            (scope_type='ORGANIZATION' AND device_id IS NULL AND scope_org_code IS NOT NULL)
        );
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_rule_scopes ON rule_definitions
    (scope_type, scope_org_code, scope_site_code, scope_zone_code, scope_profile_code, enabled);
