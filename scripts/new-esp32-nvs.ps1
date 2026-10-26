param(
    [Parameter(Mandatory)][ValidateSet('TH-001', 'DR-001')][string]$DeviceId,
    [string]$WifiSsid,
    [SecureString]$WifiPassword,
    [SecureString]$MqttPassword,
    [ValidatePattern('^[A-Za-z0-9.-]+$')][string]$MqttHost = '192.168.1.40',
    [switch]$UseExistingMqttSecret,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$firmware = Join-Path $root 'firmware'
$slug = $DeviceId.ToLowerInvariant().Replace('-', '')
$template = Join-Path $firmware "config/nvs.$slug.template.csv"
$csv = Join-Path $firmware "config/nvs.$DeviceId.local.csv"
$image = Join-Path $firmware "config/nvs.$DeviceId.bin"
$caSource = Join-Path $root 'mosquitto/certs/ca.crt'
$caTarget = Join-Path $firmware 'certs/mqtt_ca.pem'
$generator = Join-Path $env:USERPROFILE '.platformio/packages/framework-espidf/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py'
$python = Join-Path $firmware '.venv/Scripts/python.exe'

function ConvertFrom-LocalSecureString([SecureString]$Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
function ConvertTo-CsvField([string]$Value) {
    if ($Value -match '[\r\n]') { throw 'Credential values must not contain newlines' }
    '"' + $Value.Replace('"', '""') + '"'
}

if (-not $WifiSsid) { $WifiSsid = Read-Host 'Wi-Fi SSID (2.4 GHz)' }
if (-not $WifiPassword) { $WifiPassword = Read-Host 'Wi-Fi password' -AsSecureString }
if ($UseExistingMqttSecret) {
    $secretFile = Join-Path $root '.secrets/mqtt-simulator.env'
    if (-not (Test-Path $secretFile)) { throw 'Existing MQTT simulator secret file is missing' }
    $key = if ($DeviceId -eq 'TH-001') { 'TH001_MQTT_PASSWORD' } else { 'DR001_MQTT_PASSWORD' }
    $line = Get-Content $secretFile | Where-Object { $_.StartsWith("$key=") } | Select-Object -First 1
    if (-not $line) { throw "Existing MQTT password for $DeviceId is missing" }
    $mqttPlain = $line.Substring($key.Length + 1)
} else {
    if (-not $MqttPassword) { $MqttPassword = Read-Host "MQTT password for $DeviceId" -AsSecureString }
    $mqttPlain = ConvertFrom-LocalSecureString $MqttPassword
}
$wifiPlain = ConvertFrom-LocalSecureString $WifiPassword

try {
    foreach ($path in @($template, $caSource, $generator, $python)) {
        if (-not (Test-Path $path)) { throw "Required file missing: $path" }
    }
    foreach ($path in @($csv, $image)) {
        if ((Test-Path $path) -and -not $Force) {
            throw "Refusing to overwrite $path; back it up and use -Force intentionally"
        }
        if ((Test-Path $path) -and $Force) {
            Copy-Item -LiteralPath $path -Destination "$path.bak-$(Get-Date -Format yyyyMMddHHmmss)"
        }
    }
    Copy-Item -LiteralPath $caSource -Destination $caTarget -Force
    $content = Get-Content -Raw $template
    $content = $content.Replace('__WIFI_SSID__', (ConvertTo-CsvField $WifiSsid))
    $content = $content.Replace('__WIFI_PASSWORD__', (ConvertTo-CsvField $wifiPlain))
    $content = $content.Replace('__MQTT_PASSWORD__', (ConvertTo-CsvField $mqttPlain))
    $content = $content -replace '(?m)^mqtt_uri,data,string,.*$', "mqtt_uri,data,string,mqtts://${MqttHost}:8883"
    [IO.File]::WriteAllText($csv, $content, [Text.UTF8Encoding]::new($false))
    Push-Location $firmware
    try { & $python $generator generate $csv $image 0x6000 }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'NVS partition generator failed' }
    if ((Get-Item $image).Length -ne 0x6000) { throw 'Generated NVS image size is not 0x6000 bytes' }
    Write-Host "NVS image ready for $DeviceId at $image (24576 bytes)."
    Write-Host 'Secrets were not printed. Protect the .local.csv and .bin files; both are ignored by Git.'
} finally {
    $wifiPlain = $null
    $mqttPlain = $null
}
