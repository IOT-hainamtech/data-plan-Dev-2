param(
    [Parameter(Mandatory=$true)][string]$Destination,
    [switch]$IncludePrivateSecrets
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$targetRoot = [IO.Path]::GetFullPath($Destination)
if ($targetRoot -eq $root -or $root.StartsWith($targetRoot + [IO.Path]::DirectorySeparatorChar)) {
    throw 'Destination must not be the repository or one of its parents'
}
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$stage = Join-Path ([IO.Path]::GetTempPath()) "nw-config-$stamp"
$archive = Join-Path $targetRoot "nextwireless-config-$stamp.zip"
New-Item -ItemType Directory -Path $stage,$targetRoot -Force | Out-Null
try {
    foreach ($path in @('compose.yaml','compose.override.yaml','.env.example','config','mosquitto/acl','mosquitto/mosquitto.conf','docs/hardware')) {
        $source = Join-Path $root $path
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $stage -Recurse }
    }
    if ($IncludePrivateSecrets) {
        Write-Warning 'The archive contains private secrets; store and encrypt it appropriately.'
        foreach ($path in @('.env','.secrets')) {
            $source = Join-Path $root $path
            if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $stage -Recurse }
        }
    }
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $archive
    Write-Host "Configuration backup created: $archive"
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
