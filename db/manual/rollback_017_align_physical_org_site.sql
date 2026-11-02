-- Roll back registry identity only. Historical payloads were never rewritten.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('nextwireless-org-site-cutover'));

DO $$ BEGIN
  IF (SELECT count(*) FROM devices WHERE device_id IN ('TH-001','DR-001')
      AND org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001') <> 2 THEN
    RAISE EXCEPTION 'rollback preflight: devices are not both on the target site';
  END IF;
END $$;

UPDATE devices SET org_code='hainam',site_code='hq'
WHERE device_id IN ('TH-001','DR-001')
  AND org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001';

UPDATE integration_identity_mappings
SET internal_id='hainam',updated_at=CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
WHERE system_code='nextgrp' AND entity_type='ORGANIZATION'
  AND internal_id='NW-LOCAL-ORG-001' AND external_id='NW-LOCAL-ORG-001'
  AND NOT EXISTS (SELECT 1 FROM integration_identity_mappings x
    WHERE x.system_code='nextgrp' AND x.entity_type='ORGANIZATION' AND x.internal_id='hainam');
UPDATE integration_identity_mappings
SET internal_id='hq',updated_at=CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
WHERE system_code='nextgrp' AND entity_type='SITE'
  AND internal_id='NW-LOCAL-SITE-001' AND external_id='NW-LOCAL-SITE-001'
  AND NOT EXISTS (SELECT 1 FROM integration_identity_mappings x
    WHERE x.system_code='nextgrp' AND x.entity_type='SITE' AND x.internal_id='hq');

COMMIT;
-- Target registry rows are retained for audit and safe reruns; no history is deleted.
