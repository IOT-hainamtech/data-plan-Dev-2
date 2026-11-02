\set ON_ERROR_STOP on
BEGIN;
INSERT INTO organizations(org_code,display_name,enabled,metadata_json)
VALUES('hainam','Hai Nam - IoT Physical Devices',TRUE,'{"fixture":"physical"}')
ON CONFLICT(org_code) DO UPDATE SET display_name=EXCLUDED.display_name,enabled=TRUE;
INSERT INTO sites(org_code,site_code,display_name,timezone,enabled,metadata_json)
VALUES('hainam','hq','HQ','UTC',TRUE,'{"fixture":"physical"}')
ON CONFLICT(org_code,site_code) DO UPDATE SET enabled=TRUE;
INSERT INTO zones(org_code,site_code,zone_code,display_name,enabled) VALUES
 ('hainam','hq','office','Office',TRUE),('hainam','hq','entrance','Entrance',TRUE)
ON CONFLICT(org_code,site_code,zone_code) DO UPDATE SET enabled=TRUE;

UPDATE devices SET org_code='hainam',site_code='hq',zone_code='office',profile_code='TEMP_HUM_SENSOR',enabled=TRUE
 WHERE device_id='TH-001';
UPDATE devices SET org_code='hainam',site_code='hq',zone_code='entrance',profile_code='DOOR_SENSOR',enabled=TRUE
 WHERE device_id='DR-001';

INSERT INTO integration_identity_mappings
 (system_code,entity_type,internal_id,external_id,config_version,confirmed,enabled)
VALUES ('nextgrp','ORGANIZATION','hainam','NW-LOCAL-ORG-001',1,TRUE,TRUE),
       ('nextgrp','SITE','hq','NW-LOCAL-SITE-001',1,TRUE,TRUE)
ON CONFLICT DO NOTHING;

INSERT INTO alarm_episode_mappings
 (internal_episode_key,wire_alarm_key,episode_started_at,rule_code,device_id) VALUES
 ('fixture-s1','S1_TEMP_HIGH:TH-001:1000',TIMESTAMP '2026-10-31 01:00:00','S1_TEMP_HIGH','TH-001'),
 ('fixture-demo','S1_TEMP_HIGH:TH-001:2000',TIMESTAMP '2026-10-31 02:00:00','S1_TEMP_HIGH','TH-001')
ON CONFLICT DO NOTHING;
INSERT INTO alarm_occurrences
 (alarm_key,episode_key,occurrence_kind,rule_code,device_id,severity,event_type,triggered_at,payload_json)
VALUES
 ('fixture-hainam-trigger','fixture-s1','TRIGGERED','S1_TEMP_HIGH','TH-001','HIGH','TEMPERATURE_HIGH',
  TIMESTAMP '2026-10-31 01:05:00','{"org_code":"hainam","alarm_key":"S1_TEMP_HIGH:TH-001:1000"}'),
 ('fixture-demo-trigger','fixture-demo','TRIGGERED','S1_TEMP_HIGH','TH-001','HIGH','TEMPERATURE_HIGH',
  TIMESTAMP '2026-10-31 02:05:00','{"org_code":"demo","alarm_key":"S1_TEMP_HIGH:TH-001:2000"}')
ON CONFLICT(alarm_key) DO NOTHING;
INSERT INTO alarm_outbox(alarm_key,payload_json,delivery_state,next_attempt_at,created_at,wire_alarm_key)
VALUES
 ('fixture-hainam-trigger','{"org_code":"hainam","alarm_key":"S1_TEMP_HIGH:TH-001:1000"}','PENDING',
  TIMESTAMP '2026-10-31 01:05:00',TIMESTAMP '2026-10-31 01:05:00','S1_TEMP_HIGH:TH-001:1000'),
 ('fixture-demo-trigger','{"org_code":"demo","alarm_key":"S1_TEMP_HIGH:TH-001:2000"}','PENDING',
  TIMESTAMP '2026-10-31 02:05:00',TIMESTAMP '2026-10-31 02:05:00','S1_TEMP_HIGH:TH-001:2000')
ON CONFLICT(alarm_key) DO NOTHING;
COMMIT;
