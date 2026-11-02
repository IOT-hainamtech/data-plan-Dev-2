param([string]$ProjectName = "nw-orgsite-it-$PID")
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($ProjectName -notmatch '^nw-orgsite-it-[A-Za-z0-9_-]+$') {
    throw 'ProjectName must start with nw-orgsite-it-'
}
$volumes = @("${ProjectName}_postgres_data", "${ProjectName}_mqtt_data")
$env:PG_PASSWORD = 'org-site-isolated-test-only'

function Invoke-PsqlFile([string]$Path) {
    Get-Content -LiteralPath $Path -Raw | docker compose -p $ProjectName exec -T postgres `
        psql -X -v ON_ERROR_STOP=1 -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw "psql file failed: $Path" }
}
function Invoke-Psql([string]$Sql) {
    $Sql | docker compose -p $ProjectName exec -T postgres psql -X -v ON_ERROR_STOP=1 `
        -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw 'psql assertion failed' }
}

try {
    docker compose -p $ProjectName up -d postgres
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start isolated PostgreSQL' }
    $healthy = $false
    for ($i=0; $i -lt 45; $i++) {
        $row = docker compose -p $ProjectName ps --format json postgres | ConvertFrom-Json
        if ($row.Health -eq 'healthy') { $healthy=$true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw 'Isolated PostgreSQL did not become healthy' }

    Invoke-PsqlFile (Join-Path $root 'tests/fixtures/org_site_cutover_seed.sql')
    Invoke-PsqlFile (Join-Path $root 'db/manual/017_align_physical_org_site.sql')
    Invoke-Psql @"
DO `$`$ BEGIN
 IF (SELECT count(*) FROM devices WHERE device_id IN ('TH-001','DR-001') AND org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001') <> 2 THEN RAISE EXCEPTION 'device cutover failed'; END IF;
 IF (SELECT count(*) FROM zones WHERE org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001' AND zone_code IN ('office','entrance')) <> 2 THEN RAISE EXCEPTION 'zones not preserved'; END IF;
 IF (SELECT count(*) FROM rule_definitions WHERE rule_code IN ('S1_TEMP_HIGH','S6_DOOR_OPEN')) <> 2 THEN RAISE EXCEPTION 'rules changed'; END IF;
 IF (SELECT count(*) FROM alarm_occurrences WHERE alarm_key IN ('fixture-hainam-trigger','fixture-demo-trigger') AND payload_json->>'org_code' IN ('hainam','demo')) <> 2 THEN RAISE EXCEPTION 'alarm history changed'; END IF;
 IF (SELECT count(*) FROM alarm_outbox WHERE alarm_key IN ('fixture-hainam-trigger','fixture-demo-trigger') AND delivery_state='PENDING') <> 2 THEN RAISE EXCEPTION 'outbox history changed'; END IF;
 IF EXISTS (SELECT 1 FROM devices d LEFT JOIN sites s ON (s.org_code,s.site_code)=(d.org_code,d.site_code) WHERE d.device_id IN ('TH-001','DR-001') AND s.site_code IS NULL) THEN RAISE EXCEPTION 'site FK orphan'; END IF;
 IF EXISTS (SELECT 1 FROM devices d LEFT JOIN zones z ON (z.org_code,z.site_code,z.zone_code)=(d.org_code,d.site_code,d.zone_code) WHERE d.device_id IN ('TH-001','DR-001') AND z.zone_code IS NULL) THEN RAISE EXCEPTION 'zone FK orphan'; END IF;
END `$`$;
"@

    # A second pass must be a no-op and remain valid.
    Invoke-PsqlFile (Join-Path $root 'db/manual/017_align_physical_org_site.sql')

    docker compose -p $ProjectName run --rm --no-deps --build `
      -v "${root}/app:/app" -v "${root}/tests:/app/tests" `
      -e DB_HOST=postgres -e DB_PORT=5432 -e DB_NAME=nextwireless -e DB_USER=nw_user `
      -e DB_PASSWORD=org-site-isolated-test-only -e RUN_DB_TESTS=1 `
      -e DP_INBOUND_TOKEN=isolated-test-token -e DP_INBOUND_ALLOWED_ORGS=NW-LOCAL-ORG-001 `
      data-plane python -m unittest -v tests.integration.test_org_site_cutover_api
    if ($LASTEXITCODE -ne 0) { throw 'Isolated API/authorization test failed' }

    # New post-cutover history must survive registry rollback unchanged.
    Invoke-Psql @"
INSERT INTO telemetry(device_id,boot_id,seq,metric,value_number,measured_at,received_at,ts_source,ts_suspect)
VALUES('TH-001','cutover1',1,'temperature',31.5,TIMESTAMP '2026-11-02 04:00:00',TIMESTAMP '2026-11-02 04:00:01','ntp',FALSE);
INSERT INTO alarm_episode_mappings(internal_episode_key,wire_alarm_key,episode_started_at,rule_code,device_id)
VALUES('fixture-new','S1_TEMP_HIGH:TH-001:3000',TIMESTAMP '2026-11-02 04:00:00','S1_TEMP_HIGH','TH-001');
INSERT INTO alarm_occurrences(alarm_key,episode_key,occurrence_kind,rule_code,device_id,severity,event_type,triggered_at,payload_json)
VALUES('fixture-new-trigger','fixture-new','TRIGGERED','S1_TEMP_HIGH','TH-001','HIGH','TEMPERATURE_HIGH',TIMESTAMP '2026-11-02 04:05:00','{"org_code":"NW-LOCAL-ORG-001","alarm_key":"S1_TEMP_HIGH:TH-001:3000"}');
INSERT INTO alarm_outbox(alarm_key,payload_json,delivery_state,next_attempt_at,created_at,wire_alarm_key)
VALUES('fixture-new-trigger','{"org_code":"NW-LOCAL-ORG-001","alarm_key":"S1_TEMP_HIGH:TH-001:3000"}','PENDING',TIMESTAMP '2026-11-02 04:05:00',TIMESTAMP '2026-11-02 04:05:00','S1_TEMP_HIGH:TH-001:3000');
"@
    Invoke-PsqlFile (Join-Path $root 'db/manual/rollback_017_align_physical_org_site.sql')
    Invoke-PsqlFile (Join-Path $root 'db/manual/rollback_017_align_physical_org_site.sql')
    Invoke-Psql @"
DO `$`$ BEGIN
 IF (SELECT count(*) FROM devices WHERE device_id IN ('TH-001','DR-001') AND org_code='hainam' AND site_code='hq') <> 2 THEN RAISE EXCEPTION 'registry rollback failed'; END IF;
 IF NOT EXISTS (SELECT 1 FROM telemetry WHERE device_id='TH-001' AND boot_id='cutover1' AND seq=1) THEN RAISE EXCEPTION 'new telemetry lost'; END IF;
 IF NOT EXISTS (SELECT 1 FROM alarm_occurrences WHERE alarm_key='fixture-new-trigger' AND payload_json->>'org_code'='NW-LOCAL-ORG-001') THEN RAISE EXCEPTION 'new alarm changed/lost'; END IF;
 IF NOT EXISTS (SELECT 1 FROM alarm_outbox WHERE alarm_key='fixture-new-trigger' AND delivery_state='PENDING') THEN RAISE EXCEPTION 'new outbox changed/lost'; END IF;
END `$`$;
"@

    # Force a late validation failure. The transaction must roll back target zones.
    Invoke-Psql @"
DELETE FROM zones WHERE org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001';
DELETE FROM sites WHERE org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001';
DELETE FROM organizations WHERE org_code='NW-LOCAL-ORG-001';
INSERT INTO organizations(org_code,display_name,enabled) VALUES('NW-LOCAL-ORG-001','conflict',TRUE);
INSERT INTO sites(org_code,site_code,display_name,timezone,enabled) VALUES('NW-LOCAL-ORG-001','NW-LOCAL-SITE-001','conflict','UTC',FALSE);
"@
    Get-Content -LiteralPath (Join-Path $root 'db/manual/017_align_physical_org_site.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -X -v ON_ERROR_STOP=1 -U nw_user -d nextwireless
    if ($LASTEXITCODE -eq 0) { throw 'Expected late migration failure did not occur' }
    Invoke-Psql @"
DO `$`$ BEGIN
 IF EXISTS (SELECT 1 FROM zones WHERE org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001') THEN RAISE EXCEPTION 'failed transaction leaked zones'; END IF;
 IF (SELECT count(*) FROM devices WHERE device_id IN ('TH-001','DR-001') AND org_code='hainam' AND site_code='hq') <> 2 THEN RAISE EXCEPTION 'failed transaction changed devices'; END IF;
END `$`$;
DELETE FROM sites WHERE org_code='NW-LOCAL-ORG-001' AND site_code='NW-LOCAL-SITE-001';
DELETE FROM organizations WHERE org_code='NW-LOCAL-ORG-001';
"@
    Invoke-PsqlFile (Join-Path $root 'db/manual/017_align_physical_org_site.sql')
    Invoke-PsqlFile (Join-Path $root 'db/manual/rollback_017_align_physical_org_site.sql')

    # Conflicting internal mapping must fail closed and roll device updates back.
    Invoke-Psql @"
INSERT INTO integration_identity_mappings(system_code,entity_type,internal_id,external_id,config_version,confirmed,enabled)
VALUES('nextgrp','ORGANIZATION','NW-LOCAL-ORG-001','OTHER-EXTERNAL-ORG',1,TRUE,TRUE);
"@
    Get-Content -LiteralPath (Join-Path $root 'db/manual/017_align_physical_org_site.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -X -v ON_ERROR_STOP=1 -U nw_user -d nextwireless
    if ($LASTEXITCODE -eq 0) { throw 'Expected identity mapping conflict did not occur' }
    Invoke-Psql @"
DO `$`$ BEGIN
 IF (SELECT count(*) FROM devices WHERE device_id IN ('TH-001','DR-001') AND org_code='hainam' AND site_code='hq') <> 2 THEN RAISE EXCEPTION 'mapping conflict leaked device cutover'; END IF;
END `$`$;
DELETE FROM integration_identity_mappings WHERE system_code='nextgrp' AND entity_type='ORGANIZATION' AND internal_id='NW-LOCAL-ORG-001' AND external_id='OTHER-EXTERNAL-ORG';
"@
    Write-Host "ORG_SITE_CUTOVER_TEST=PASS project=$ProjectName"
} finally {
    $old = $ErrorActionPreference; $ErrorActionPreference='SilentlyContinue'
    docker compose -p $ProjectName stop postgres *> $null
    docker compose -p $ProjectName rm -f postgres *> $null
    foreach ($volume in $volumes) {
        $known = docker volume ls --format '{{.Name}}' | Where-Object { $_ -eq $volume }
        if ($known) { docker volume rm $volume *> $null }
    }
    $network = "${ProjectName}_default"
    if (docker network ls --format '{{.Name}}' | Where-Object { $_ -eq $network }) {
        docker network rm $network *> $null
    }
    $ErrorActionPreference=$old
    Remove-Item Env:PG_PASSWORD -ErrorAction SilentlyContinue
}
