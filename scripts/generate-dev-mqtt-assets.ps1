param(
    [string]$LanIp = $env:MQTT_SERVER_LAN_IP
)
$ErrorActionPreference = 'Stop'
$dockerArgs = @('run', '--rm')
if ($LanIp) {
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($LanIp, [ref]$parsed) -or
        $parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw "LanIp must be a valid IPv4 address"
    }
    $dockerArgs += @('-e', "MQTT_SERVER_LAN_IP=$LanIp")
}
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dockerArgs += @('-v', "${root}:/work", 'alpine:3.22', 'sh', '-c',
    "apk add --no-cache openssl >/dev/null && sed 's/`r$//' /work/scripts/generate-dev-mqtt-assets.sh > /tmp/generate-dev-mqtt-assets.sh && sh /tmp/generate-dev-mqtt-assets.sh")
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) { throw 'MQTT asset generation failed' }
Write-Host 'Assets created under .secrets/ and mosquitto/certs/ (both ignored by Git).'
