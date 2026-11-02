-- MANUAL CUTOVER ONLY. This file is intentionally outside db/migrations.
-- Run only after backup, transition ACL activation, and an approved NVS cutover window.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('nextwireless-org-site-cutover'));

DO $$
DECLARE
    unexpected bigint;
    missing bigint;
BEGIN
    SELECT count(*) INTO unexpected FROM devices
      WHERE org_code='hainam' AND site_code='hq'
        AND device_id NOT IN ('TH-001','DR-001');
    IF unexpected <> 0 THEN
        RAISE EXCEPTION 'preflight: unexpected devices remain in hainam/hq: %', unexpected;
    END IF;

    SELECT count(*) INTO missing FROM (VALUES
      ('TH-001','office'),('DR-001','entrance')) AS expected(device_id,zone_code)
      WHERE NOT EXISTS (
        SELECT 1 FROM devices d WHERE d.device_id=expected.device_id
          AND d.zone_code=expected.zone_code
          AND ((d.org_code='hainam' AND d.site_code='hq') OR
               (d.org_code='NW-LOCAL-ORG-001' AND d.site_code='NW-LOCAL-SITE-001')));
    IF missing <> 0 THEN
        RAISE EXCEPTION 'preflight: physical device identity/zone mismatch: %', missing;
    END IF;
END $$;

INSERT INTO organizations(org_code,display_name,enabled,metadata_json,config_version,payload_hash)
SELECT 'NW-LOCAL-ORG-001',display_name,TRUE,
       metadata_json || '{"aligned_from":"hainam"}'::jsonb,config_version,NULL
FROM organizations WHERE org_code='hainam'
ON CONFLICT(org_code) DO NOTHING;

INSERT INTO sites(org_code,site_code,display_name,timezone,enabled,metadata_json,config_version,payload_hash)
SELECT 'NW-LOCAL-ORG-001','NW-LOCAL-SITE-001',display_name,timezone,TRUE,
       metadata_json || '{"aligned_from":"hainam/hq"}'::jsonb,config_version,NULL
FROM sites WHERE org_code='hainam' AND site_code='hq'
ON CONFLICT(org_code,site_code) DO NOTHING;

INSERT INTO zones(org_code,site_code,zone_code,display_name,enabled,metadata_json,config_version,payload_hash)
SELECT 'NW-LOCAL-ORG-001','NW-LOCAL-SITE-001',zone_code,display_name,enabled,
       metadata_json || '{"aligned_from":"hainam/hq"}'::jsonb,config_version,NULL
FROM zones WHERE org_code='hainam' AND site_code='hq' AND zone_code IN ('office','entrance')
ON CONFLICT(org_code,site_code,zone_code) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM organizations WHERE org_code='NW-LOCAL-ORG-001' AND enabled) OR
       NOT EXISTS (SELECT 1 FROM sites WHERE org_code='NW-LOCAL-ORG-001'
                    AND site_code='NW-LOCAL-SITE-001' AND enabled) OR
       (SELECT count(*) FROM zones WHERE org_code='NW-LOCAL-ORG-001'
          AND site_code='NW-LOCAL-SITE-001' AND zone_code IN ('office','entrance')) <> 2 THEN
        RAISE EXCEPTION 'target organization/site/zones are incomplete';
    END IF;
END $$;

UPDATE devices SET org_code='NW-LOCAL-ORG-001',site_code='NW-LOCAL-SITE-001'
WHERE device_id IN ('TH-001','DR-001') AND org_code='hainam' AND site_code='hq';

-- Only identity rows for the same physical org/site are updated. Device/rule IDs stay unchanged.
UPDATE integration_identity_mappings
SET internal_id='NW-LOCAL-ORG-001',updated_at=CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
WHERE system_code='nextgrp' AND entity_type='ORGANIZATION' AND internal_id='hainam'
  AND external_id='NW-LOCAL-ORG-001'
  AND NOT EXISTS (SELECT 1 FROM integration_identity_mappings x
    WHERE x.system_code='nextgrp' AND x.entity_type='ORGANIZATION'
      AND x.internal_id='NW-LOCAL-ORG-001');
UPDATE integration_identity_mappings
SET internal_id='NW-LOCAL-SITE-001',updated_at=CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
WHERE system_code='nextgrp' AND entity_type='SITE' AND internal_id='hq'
  AND external_id='NW-LOCAL-SITE-001'
  AND NOT EXISTS (SELECT 1 FROM integration_identity_mappings x
    WHERE x.system_code='nextgrp' AND x.entity_type='SITE'
      AND x.internal_id='NW-LOCAL-SITE-001');

DO $$ BEGIN
  IF (SELECT count(*) FROM devices WHERE device_id IN ('TH-001','DR-001')
      AND org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001') <> 2 THEN
    RAISE EXCEPTION 'postflight: both physical devices were not aligned';
  END IF;
  IF EXISTS (SELECT 1 FROM integration_identity_mappings
      WHERE system_code='nextgrp' AND entity_type='ORGANIZATION'
        AND internal_id='hainam' AND external_id='NW-LOCAL-ORG-001') OR
     EXISTS (SELECT 1 FROM integration_identity_mappings
      WHERE system_code='nextgrp' AND entity_type='SITE'
        AND internal_id='hq' AND external_id='NW-LOCAL-SITE-001') THEN
    RAISE EXCEPTION 'postflight: identity mapping conflict prevented alignment';
  END IF;
END $$;
COMMIT;

-- Deliberately unchanged: telemetry/events/status history, runtime state,
-- alarm_occurrences, alarm_episode_mappings, alarm_outbox and all demo data.
