$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
docker run --rm -v "${root}:/work" alpine:3.22 sh -c "apk add --no-cache openssl >/dev/null && sh /work/scripts/generate-dev-mqtt-assets.sh"
if ($LASTEXITCODE -ne 0) { throw 'MQTT asset generation failed' }
Write-Host 'Assets created under .secrets/ and mosquitto/certs/ (both ignored by Git).'
