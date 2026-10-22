param(
    [Parameter(Mandatory=$true)][ValidateSet('grant','password','revoke')][string]$Action,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$Username,
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$Org = 'demo',
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$Site = 'demo-site',
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$DeviceId
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$aclPath = Join-Path $root 'mosquitto/acl'
$container = docker compose -f (Join-Path $root 'compose.yaml') ps -q mqtt
if (-not $container) { throw 'MQTT container is not running' }
if ($Action -eq 'grant' -and -not $DeviceId) { throw '-DeviceId is required for grant' }
if ($Action -eq 'revoke') {
    docker exec $container mosquitto_passwd -D /mosquitto/data/security/passwords $Username
    if ($LASTEXITCODE -ne 0) { throw 'Account revocation failed' }
} else {
    $password = Read-Host "Password for $Username" -AsSecureString
    $plain = [System.Net.NetworkCredential]::new('', $password).Password
    try {
        "$plain`n$plain" | docker exec -i $container mosquitto_passwd /mosquitto/data/security/passwords $Username
        if ($LASTEXITCODE -ne 0) { throw 'Password update failed' }
    } finally { $plain = $null }
    if ($Action -eq 'grant') {
        $text = Get-Content -LiteralPath $aclPath -Raw
        if ($text -notmatch "(?m)^user $([regex]::Escape($Username))$") {
            Add-Content -LiteralPath $aclPath -Value "`nuser $Username`ntopic write nw/v1/$Org/$Site/$DeviceId/#`n"
        }
    }
}
docker exec $container kill -HUP 1
if ($LASTEXITCODE -ne 0) { throw 'Mosquitto reload failed' }
Write-Host "MQTT account action '$Action' applied without stopping the stack."
