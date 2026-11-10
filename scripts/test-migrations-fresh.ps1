param([string]$ProjectName = "nw-migration-test-$PID")
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$volumes = @("${ProjectName}_postgres_data", "${ProjectName}_mqtt_data", "${ProjectName}_mqtt_ca")
$env:PG_PASSWORD = 'migration-test-only-password'
try {
    docker compose -p $ProjectName up -d postgres
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start isolated PostgreSQL' }
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        $status = docker compose -p $ProjectName ps --format json postgres | ConvertFrom-Json
        if ($status.Health -eq 'healthy') { $healthy = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw 'Isolated PostgreSQL did not become healthy' }

    docker compose -p $ProjectName exec -T postgres sh -c `
      'for f in /docker-entrypoint-initdb.d/*.sql; do psql -v ON_ERROR_STOP=1 -U nw_user -d nextwireless -f "$f" >/dev/null; done'
    if ($LASTEXITCODE -ne 0) { throw 'Migration idempotency pass failed' }

    docker compose -p $ProjectName run --rm --no-deps `
      -v "${root}/app:/app" -v "${root}/tests:/app/tests" `
      -e DB_HOST=postgres -e DB_PORT=5432 -e DB_NAME=nextwireless -e DB_USER=nw_user `
      -e DB_PASSWORD=migration-test-only-password -e RUN_DB_TESTS=1 `
      data-plane python -m unittest -v tests.integration.test_ingestor_contract `
      tests.integration.test_rule_engine_integration tests.integration.test_outbox_integration `
      tests.integration.test_rule_version_freeze tests.integration.test_status_tracker `
      tests.integration.test_multi_site_integration
    if ($LASTEXITCODE -ne 0) { throw 'Fresh-schema ingestion/rule tests failed' }

    docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
      -U nw_user -d nextwireless -c `
      "SELECT conname FROM pg_constraint WHERE conrelid='telemetry_reports'::regclass AND conname IN ('ck_telemetry_reports_quality','uq_telemetry_report') ORDER BY conname;"
    if ($LASTEXITCODE -ne 0) { throw 'Fresh-schema constraint verification failed' }
    docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
      -U nw_user -d nextwireless -c `
      "SELECT retention_days,enabled FROM telemetry_retention_config; SELECT count(*) AS active_retention_jobs FROM timescaledb_information.jobs WHERE proc_name='policy_retention' AND hypertable_name='telemetry';"
    if ($LASTEXITCODE -ne 0) { throw 'Disabled retention verification failed' }

    Get-Content -LiteralPath (Join-Path $root 'db/rollback/017_fast_presence.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
        -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw 'Fast Presence rollback failed' }
    docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
      -U nw_user -d nextwireless -c `
      "SELECT count(*)=0 AS rollback_removed_presence_columns FROM information_schema.columns WHERE table_name='device_status' AND column_name LIKE 'presence_%';"
    if ($LASTEXITCODE -ne 0) { throw 'Fast Presence rollback verification failed' }
    Get-Content -LiteralPath (Join-Path $root 'db/migrations/017_fast_presence.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
        -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw 'Fast Presence reapply after rollback failed' }

    Get-Content -LiteralPath (Join-Path $root 'db/rollback/018_realtime_event_outbox.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
        -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw 'Realtime Event Outbox rollback failed' }
    docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
      -U nw_user -d nextwireless -c `
      "SELECT to_regclass('public.device_event_outbox') IS NULL AS rollback_removed_event_outbox;"
    if ($LASTEXITCODE -ne 0) { throw 'Realtime Event Outbox rollback verification failed' }
    Get-Content -LiteralPath (Join-Path $root 'db/migrations/018_realtime_event_outbox.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
        -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw 'Realtime Event Outbox reapply failed' }

    # This project is disposable. Remove test-generated episodes before testing
    # the permitted (pre-acceptance) rollback path; production rollback must
    # remain blocked once frozen provenance has been used.
    docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
      -U nw_user -d nextwireless -c `
      "DELETE FROM alarm_outbox; DELETE FROM alarm_occurrences; DELETE FROM alarm_episode_mappings; UPDATE rule_runtime_state SET hold_started_at=NULL,is_active=FALSE,recovery_started_at=NULL,episode_key=NULL,episode_started_at=NULL,current_state=NULL,frozen_rule_version=NULL,frozen_rule_payload_hash=NULL,frozen_rule_config_json=NULL,frozen_identity_json=NULL,episode_provenance=NULL;"
    if ($LASTEXITCODE -ne 0) { throw 'Isolated Rule episode cleanup failed' }
    Get-Content -LiteralPath (Join-Path $root 'db/rollback/019_rule_version_episode_freeze.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
        -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw 'Rule episode freeze rollback failed' }
    docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
      -U nw_user -d nextwireless -c `
      "SELECT to_regclass('public.integration_identity_mapping_versions') IS NULL AS rollback_removed_mapping_history;"
    if ($LASTEXITCODE -ne 0) { throw 'Rule episode freeze rollback verification failed' }
    Get-Content -LiteralPath (Join-Path $root 'db/migrations/019_rule_version_episode_freeze.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
        -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw 'Rule episode freeze reapply failed' }

    Get-Content -LiteralPath (Join-Path $root 'db/rollback/016_retention_configuration.sql') -Raw | `
      docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
        -U nw_user -d nextwireless
    if ($LASTEXITCODE -ne 0) { throw 'Retention rollback failed' }
    docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
      -U nw_user -d nextwireless -c `
      "SELECT to_regclass('public.telemetry_retention_config') IS NULL AS rollback_removed_control_table;"
    if ($LASTEXITCODE -ne 0) { throw 'Retention rollback verification failed' }
    Write-Host "Fresh migration test passed in isolated project $ProjectName"
} finally {
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    & docker compose -p $ProjectName stop postgres *> $null
    & docker compose -p $ProjectName rm -f postgres *> $null
    foreach ($volume in $volumes) {
        $known = docker volume ls --format '{{.Name}}' | Where-Object { $_ -eq $volume }
        if ($known) { & docker volume rm $volume *> $null }
    }
    $network = "${ProjectName}_default"
    $knownNetwork = docker network ls --format '{{.Name}}' | Where-Object { $_ -eq $network }
    if ($knownNetwork) { & docker network rm $network *> $null }
    $ErrorActionPreference = $savedPreference
    Remove-Item Env:PG_PASSWORD -ErrorAction SilentlyContinue
}
