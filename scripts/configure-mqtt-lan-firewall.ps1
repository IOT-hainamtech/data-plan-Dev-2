param(
    [Parameter(Mandatory)][string]$LocalIp,
    [Parameter(Mandatory)][string]$RemoteSubnet,
    [switch]$Apply,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$ruleName = 'nextWireless MQTT TLS 8883 (IoT LAN)'

if (-not $Apply -and -not $Remove) {
    Write-Host "PREVIEW: inbound TCP 8883; profile Private; local $LocalIp; remote $RemoteSubnet"
    Write-Host 'No firewall change made. Re-run with -Apply after explicit approval.'
    exit 0
}
if ($Apply -and $Remove) { throw 'Choose either -Apply or -Remove' }

if ($Remove) {
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    Write-Host "Removed firewall rule: $ruleName"
    exit 0
}

if ((Get-NetConnectionProfile -InterfaceAlias 'Wi-Fi').NetworkCategory -ne 'Private') {
    throw 'Wi-Fi network profile must be Private'
}
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    $address = $existing | Get-NetFirewallAddressFilter
    $port = $existing | Get-NetFirewallPortFilter
    $matches = $existing.Enabled -eq 'True' -and $existing.Direction -eq 'Inbound' -and
        $existing.Action -eq 'Allow' -and $existing.Profile -eq 'Private' -and
        $port.Protocol -eq 'TCP' -and $port.LocalPort -eq '8883' -and
        $address.LocalAddress -contains $LocalIp -and
        $address.RemoteAddress -contains $RemoteSubnet
    if ($matches) {
        Write-Host "Firewall rule already matches requested scope: $ruleName"
        exit 0
    }
    throw "A rule named '$ruleName' exists with a different scope; inspect or remove it explicitly"
}
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort 8883 -LocalAddress $LocalIp `
    -RemoteAddress $RemoteSubnet -Profile Private | Out-Null
Write-Host "Applied firewall rule: $ruleName"
