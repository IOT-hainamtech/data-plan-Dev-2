-- Idempotent staging seed for Organization NW-LOCAL-ORG-001 (Dev 2 ↔ Dev 1 nextGRP Integration)
-- Preserves runtime data ('demo', 'v13-test') untouched.
-- All operations use ON CONFLICT DO NOTHING to guarantee:
--   1. Re-runs never reset config_version.
--   2. Re-runs never overwrite synced Profile/Rule data.
--   3. Re-runs never re-enable disabled devices.

-- 1. Organization (Confirmed by Dev 1)
INSERT INTO organizations (org_code, display_name, enabled, config_version, metadata_json)
VALUES ('NW-LOCAL-ORG-001', 'nextGRP Local Organization 001', TRUE, 1, '{"env":"staging"}'::jsonb)
ON CONFLICT (org_code) DO NOTHING;

-- 2. Site (Confirmed by Dev 1)
INSERT INTO sites (org_code, site_code, display_name, timezone, enabled, config_version, metadata_json)
VALUES ('NW-LOCAL-ORG-001', 'NW-LOCAL-SITE-001', 'nextGRP Local Site 001', 'Asia/Ho_Chi_Minh', TRUE, 1, '{"env":"staging"}'::jsonb)
ON CONFLICT (org_code, site_code) DO NOTHING;

-- 3. Zones (Schema dependency: Dev 2 devices table has a foreign key to zones.
--    Dev 1 contract specifies zone=null for physical sensors. We seed a single
--    internal 'DEFAULT' zone to satisfy schema constraints without inventing physical lab zones.)
INSERT INTO zones (org_code, site_code, zone_code, display_name, enabled, config_version, metadata_json)
VALUES ('NW-LOCAL-ORG-001', 'NW-LOCAL-SITE-001', 'DEFAULT', 'Default Staging Zone', TRUE, 1, '{"env":"staging"}'::jsonb)
ON CONFLICT (org_code, site_code, zone_code) DO NOTHING;

-- 4. Device Profiles (S1 & S6)
INSERT INTO device_profiles (profile_code, display_name, enabled, config_version, metadata_json)
VALUES ('NW-IT-S1', 'Temperature and Humidity Sensor Profile', TRUE, 1, '{"signal_model":"TELEMETRY","protocol":"MQTT","expected_interval_s":60,"heartbeat_interval_s":60,"offline_after_missed":2}'::jsonb),
       ('NW-IT-S6', 'Door Reed Sensor Profile', TRUE, 1, '{"signal_model":"EVENT","protocol":"MQTT","expected_interval_s":60,"heartbeat_interval_s":60,"offline_after_missed":2}'::jsonb)
ON CONFLICT (profile_code) DO NOTHING;

-- 4.1 Points for S1
INSERT INTO device_profile_points (profile_code, channel, point_code, data_type, unit, min_value, max_value, required)
VALUES ('NW-IT-S1', 'telemetry', 'temperature', 'number', 'Cel', -40.0, 125.0, TRUE),
       ('NW-IT-S1', 'telemetry', 'humidity', 'number', '%RH', 0.0, 100.0, TRUE)
ON CONFLICT (profile_code, channel, point_code) DO NOTHING;

-- 4.2 Points & Events for S6
INSERT INTO device_profile_points (profile_code, channel, point_code, data_type, allowed_values, required)
VALUES ('NW-IT-S6', 'telemetry', 'door_state', 'string', '["open", "closed"]'::jsonb, TRUE)
ON CONFLICT (profile_code, channel, point_code) DO NOTHING;

INSERT INTO device_profile_events (profile_code, event_type, state_point, state_value)
VALUES ('NW-IT-S6', 'DOOR_OPENED', 'door_state', 'open'),
       ('NW-IT-S6', 'DOOR_CLOSED', 'door_state', 'closed')
ON CONFLICT (profile_code, event_type) DO NOTHING;

-- 5. Devices (Confirmed by Dev 1: NW-IT-TH001 and NW-IT-DR001)
INSERT INTO devices (device_id, org_code, site_code, zone_code, profile_code, enabled, connectivity, config_version, telemetry_interval_seconds, heartbeat_interval_seconds, metadata_json)
VALUES ('NW-IT-TH001', 'NW-LOCAL-ORG-001', 'NW-LOCAL-SITE-001', 'DEFAULT', 'NW-IT-S1', TRUE, 'WIFI', 1, 60, 60, '{"role":"staging_test"}'::jsonb),
       ('NW-IT-DR001', 'NW-LOCAL-ORG-001', 'NW-LOCAL-SITE-001', 'DEFAULT', 'NW-IT-S6', TRUE, 'WIFI', 1, 60, 60, '{"role":"staging_test"}'::jsonb)
ON CONFLICT (device_id) DO NOTHING;

-- 6. Rules (S1 & S6)
-- S1: temperature > 30.0 threshold rule
INSERT INTO rule_definitions (rule_code, device_id, rule_kind, metric, threshold_value, hysteresis_value, hold_seconds, clear_hold_seconds, quiet_seconds, severity, enabled, scope_type, config_version)
VALUES ('NW-IT-RULE-S1', 'NW-IT-TH001', 'THRESHOLD', 'temperature', 30.0, 0.5, 0, 0, 0, 'HIGH', TRUE, 'DEVICE', 1)
ON CONFLICT (rule_code) DO NOTHING;

-- S6: STATE_CHANGE DOOR_OPENED with open_limit_s=300 and schedule
INSERT INTO rule_definitions (rule_code, device_id, rule_kind, event_type, hold_seconds, clear_hold_seconds, quiet_seconds, severity, enabled, business_start, business_end, schedule_timezone, scope_type, config_version)
VALUES ('NW-IT-RULE-S6', 'NW-IT-DR001', 'STATE_CHANGE', 'DOOR_OPENED', 300, 0, 0, 'HIGH', TRUE, TIME '08:00:00', TIME '18:00:00', 'Asia/Ho_Chi_Minh', 'DEVICE', 1)
ON CONFLICT (rule_code) DO NOTHING;

-- 7. Identity Mappings (Confirmed = TRUE only for identities confirmed by Dev 1.
--    Unconfirmed entities have confirmed = FALSE to fail-closed.)
INSERT INTO integration_identity_mappings (system_code, entity_type, internal_id, external_id, config_version, confirmed, enabled)
VALUES
-- Confirmed by Dev 1:
('nextgrp', 'ORGANIZATION', 'NW-LOCAL-ORG-001', 'NW-LOCAL-ORG-001', 1, TRUE, TRUE),
('nextgrp', 'SITE', 'NW-LOCAL-SITE-001', 'NW-LOCAL-SITE-001', 1, TRUE, TRUE),
('nextgrp', 'DEVICE', 'NW-IT-TH001', 'NW-IT-TH001', 1, TRUE, TRUE),
('nextgrp', 'DEVICE', 'NW-IT-DR001', 'NW-IT-DR001', 1, TRUE, TRUE),
-- Pending Dev 1 Confirmation (Fail-closed: confirmed = FALSE):
('nextgrp', 'ZONE', 'DEFAULT', 'DEFAULT', 1, FALSE, TRUE),
('nextgrp', 'PROFILE', 'NW-IT-S1', 'NW-LOCAL-S1', 1, FALSE, TRUE),
('nextgrp', 'PROFILE', 'NW-IT-S6', 'NW-LOCAL-S6', 1, FALSE, TRUE),
('nextgrp', 'RULE', 'NW-IT-RULE-S1', 'NW-LOCAL-RULE-S1', 1, FALSE, TRUE),
('nextgrp', 'RULE', 'NW-IT-RULE-S6', 'NW-LOCAL-RULE-S6', 1, FALSE, TRUE)
ON CONFLICT (system_code, entity_type, internal_id) DO NOTHING;
