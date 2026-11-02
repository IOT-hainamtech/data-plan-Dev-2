-- Roll back registry identity only. Historical payloads were never rewritten.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('nextwireless-org-site-cutover'));

DO $$
DECLARE invalid bigint;
BEGIN
  SELECT count(*) INTO invalid FROM (VALUES
    ('TH-001','office'),('DR-001','entrance')) expected(device_id,zone_code)
  WHERE NOT EXISTS (SELECT 1 FROM devices d
    WHERE d.device_id=expected.device_id AND d.zone_code=expected.zone_code
      AND ((d.org_code='hainam' AND d.site_code='hq') OR
           (d.org_code='NW-LOCAL-ORG-001' AND d.site_code='NW-LOCAL-SITE-001')));
  IF invalid <> 0 THEN
    RAISE EXCEPTION 'rollback preflight: physical device identity/location mismatch: %', invalid;
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

DO $$ BEGIN
  IF (SELECT count(*) FROM devices WHERE device_id IN ('TH-001','DR-001')
      AND org_code='hainam' AND site_code='hq') <> 2 THEN
    RAISE EXCEPTION 'rollback postflight: both physical devices were not restored';
  END IF;
  IF EXISTS (SELECT 1 FROM integration_identity_mappings
      WHERE system_code='nextgrp' AND entity_type='ORGANIZATION'
        AND internal_id='NW-LOCAL-ORG-001' AND external_id='NW-LOCAL-ORG-001') OR
     EXISTS (SELECT 1 FROM integration_identity_mappings
      WHERE system_code='nextgrp' AND entity_type='SITE'
        AND internal_id='NW-LOCAL-SITE-001' AND external_id='NW-LOCAL-SITE-001') THEN
    RAISE EXCEPTION 'rollback postflight: identity mapping conflict prevented restoration';
  END IF;
END $$;

COMMIT;
-- Target registry rows are retained for audit and safe reruns; no history is deleted.
