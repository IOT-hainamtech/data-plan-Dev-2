param([string]$ProjectName = "nw-migration-test-$PID")
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$volumes = @("${ProjectName}_postgres_data", "${ProjectName}_mqtt_data")
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
      -e DB_HOST=postgres -e DB_PORT=5432 -e DB_NAME=nextwireless -e DB_USER=nw_user `
      -e DB_PASSWORD=migration-test-only-password -e RUN_DB_TESTS=1 `
      data-plane python -m unittest -v test_ingestor_contract test_rule_engine_integration
    if ($LASTEXITCODE -ne 0) { throw 'Fresh-schema ingestion/rule tests failed' }

    docker compose -p $ProjectName exec -T postgres psql -v ON_ERROR_STOP=1 `
      -U nw_user -d nextwireless -c `
      "SELECT conname FROM pg_constraint WHERE conrelid='telemetry_reports'::regclass AND conname IN ('ck_telemetry_reports_quality','uq_telemetry_report') ORDER BY conname;"
    if ($LASTEXITCODE -ne 0) { throw 'Fresh-schema constraint verification failed' }
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
