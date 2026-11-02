param([string]$ProjectName = "nw-ca-permission-test-$PID")
$ErrorActionPreference = 'Stop'
$env:PG_PASSWORD = 'ca-permission-test-only'
try {
    docker compose -p $ProjectName --profile mqtt-ca-init run --rm --no-deps mqtt-ca-init
    if ($LASTEXITCODE -ne 0) { throw 'CA initializer failed' }
    docker compose -p $ProjectName run --rm --no-deps --entrypoint python data-plane -c `
      "import os,ssl; assert os.getuid()==10001; p=os.environ['MQTT_CA_FILE']; assert open(p,'rb').read(27).startswith(b'-----BEGIN CERTIFICATE-----'); ssl.create_default_context(ssl.Purpose.SERVER_AUTH,cafile=p); print('NON_ROOT_CA_READ_AND_TLS_VERIFY_OK')"
    if ($LASTEXITCODE -ne 0) { throw 'UID 10001 could not read/validate CA' }
} finally {
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    & docker compose -p $ProjectName down -v --remove-orphans *> $null
    $ErrorActionPreference = $savedPreference
    Remove-Item Env:PG_PASSWORD -ErrorAction SilentlyContinue
}
