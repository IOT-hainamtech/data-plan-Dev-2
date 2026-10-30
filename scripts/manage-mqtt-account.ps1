param(
    [Parameter(Mandatory=$true)][ValidateSet('grant','password','revoke')][string]$Action,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$Username,
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$Org = 'demo',
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$Site = 'demo-site',
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$DeviceId,
    [SecureString]$Password
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$aclPath = Join-Path $root 'mosquitto/acl'
$container = docker compose -f (Join-Path $root 'compose.yaml') ps -q mqtt
if (-not $container) { throw 'MQTT container is not running' }
if ($Action -eq 'grant' -and -not $DeviceId) { throw '-DeviceId is required for grant' }
if ($Action -eq 'grant' -and $Username -ne $DeviceId) {
    throw 'Device MQTT username must equal DeviceId'
}
if ($Action -eq 'revoke') {
    docker exec $container mosquitto_passwd -D /mosquitto/data/security/passwords $Username
    if ($LASTEXITCODE -ne 0) { throw 'Account revocation failed' }
} else {
    if (-not $Password) {
        if ($Action -eq 'grant') {
            $bytes = [byte[]]::new(24)
            [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
            $generated = [Convert]::ToBase64String($bytes)
            $Password = ConvertTo-SecureString $generated -AsPlainText -Force
            $handoffDir = Join-Path $root '.secrets/provisioning'
            New-Item -ItemType Directory -Force -Path $handoffDir | Out-Null
            $handoffPath = Join-Path $handoffDir "$DeviceId.mqtt.dpapi"
            $Password | ConvertFrom-SecureString | Set-Content -LiteralPath $handoffPath
            $generated = $null
        } else {
            $Password = Read-Host "Password for $Username" -AsSecureString
        }
    }
    $plain = [System.Net.NetworkCredential]::new('', $Password).Password
    if ($plain.Length -lt 16) { throw 'MQTT credential must contain at least 16 characters' }
    try {
        "$plain`n$plain" | docker exec -i $container mosquitto_passwd /mosquitto/data/security/passwords $Username
        if ($LASTEXITCODE -ne 0) { throw 'Password update failed' }
    } finally { $plain = $null }
    if ($Action -eq 'grant') {
        $text = Get-Content -LiteralPath $aclPath -Raw
        $block = "user $Username`ntopic write nw/v1/$Org/$Site/$DeviceId/telemetry`n" +
                 "topic write nw/v1/$Org/$Site/$DeviceId/event`n" +
                 "topic write nw/v1/$Org/$Site/$DeviceId/status`n"
        $pattern = "(?ms)^user $([regex]::Escape($Username))\r?\n.*?(?=^user |\z)"
        $accountPattern = [regex]::new($pattern)
        if ($accountPattern.IsMatch($text)) {
            $text = $accountPattern.Replace($text, $block, 1)
        } else {
            $text = $text.TrimEnd() + "`n`n" + $block
        }
        [IO.File]::WriteAllText($aclPath, $text, [Text.UTF8Encoding]::new($false))
    }
}
docker exec $container kill -HUP 1
if ($LASTEXITCODE -ne 0) { throw 'Mosquitto reload failed' }
Write-Host "MQTT account action '$Action' applied without stopping the stack."
if ($handoffPath) { Write-Host "Encrypted one-time credential handoff: $handoffPath" }
