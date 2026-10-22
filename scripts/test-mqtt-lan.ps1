param(
    [Parameter(Mandatory)][string]$BrokerIp
)
$ErrorActionPreference = 'Stop'
$env:RUN_MQTT_INGESTION_TESTS = '1'
docker compose --profile mqtt-test run --rm `
    -e "MQTT_HOST=$BrokerIp" `
    -e RUN_MQTT_INGESTION_TESTS=1 `
    mqtt-security-test
if ($LASTEXITCODE -ne 0) { throw 'MQTT LAN integration tests failed' }
