param(
    [string]$ApiBaseUrl = 'http://127.0.0.1:8002',
    [string]$Organization = 'hainam',
    [string]$DeviceId = 'TH-001',
    [SecureString]$ApiToken,
    [ValidateRange(5, 60)][int]$PollSeconds = 5,
    [ValidateRange(1, 180)][int]$DurationMinutes = 20,
    [long]$BaselineOccurrenceId = -1,
    [string]$ReportPath,
    [switch]$ValidateOnly,
    [switch]$FormatSelfTest
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ReportPath) {
    $ReportPath = Join-Path $root ("reports/S1_PHYSICAL_ACCEPTANCE_{0}.md" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
}
$reportFullPath = [IO.Path]::GetFullPath($ReportPath)
$reportsRoot = [IO.Path]::GetFullPath((Join-Path $root 'reports'))
if (-not $reportFullPath.StartsWith($reportsRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'ReportPath must remain under the repository reports directory'
}

function ConvertFrom-LocalSecureString([SecureString]$Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Format-MonitorLine {
    param($Latest, $Temperature, $Humidity, [string]$SeqMark,
          [int]$AboveSeconds, [int]$BelowSeconds, [string]$RulePhase,
          [int]$NewOccurrenceCount)
    return (("{0}  T={1:N2}C H={2:N2}% seq={3} {4}/{5} age={6:N1}s " +
        "{7} local>30={8}s local<29={9}s DB={10} new_occ={11}") -f
        $Latest.measured_at_local,$Temperature,$Humidity,$Latest.seq,
        $Latest.online_status,$Latest.data_status,$Latest.age_seconds,
        $SeqMark,$AboveSeconds,$BelowSeconds,$RulePhase,$NewOccurrenceCount)
}

if ($FormatSelfTest) {
    $sample = [pscustomobject]@{ measured_at_local='2026-10-31T16:40:34+07:00'; seq=42
        online_status='online'; data_status='LIVE'; age_seconds=1.25 }
    $line = Format-MonitorLine $sample 30.55 61.25 'SEQ-OK' 305 0 'ACTIVE' 1
    if ($line -match '\{[0-9]' -or $line -notmatch 'T=30.55C' -or
        $line -notmatch 'H=61.25%' -or $line -notmatch 'seq=42 online/LIVE' -or
        $line -notmatch 'local>30=305s' -or $line -notmatch 'DB=ACTIVE new_occ=1') {
        throw "Monitor output format self-test failed: $line"
    }
    Write-Host "Monitor output format self-test PASS: $line"
    exit 0
}

function Invoke-ReadOnlySql {
    param([Parameter(Mandatory)][string]$Sql)
    if ($Sql -notmatch '^\s*(WITH|SELECT)\b') { throw 'Only SELECT/CTE SQL is permitted' }
    $result = & docker compose exec -T postgres psql -X -q -t -A `
        -U nw_user -d nextwireless -v ON_ERROR_STOP=1 -c $Sql
    if ($LASTEXITCODE -ne 0) { throw 'Read-only PostgreSQL query failed' }
    $json = ($result -join "`n").Trim()
    if (-not $json) { throw 'Read-only PostgreSQL query returned no JSON' }
    return $json | ConvertFrom-Json
}

function Get-RuleEvidence {
    param([long]$AfterOccurrenceId = 0)
    $sql = @"
WITH runtime AS (
  SELECT rule_code,device_id,is_active,
         hold_started_at::text,recovery_started_at::text,last_sample_at::text,
         last_fired_at::text,episode_key,episode_started_at::text
  FROM rule_runtime_state
  WHERE rule_code='S1_TEMP_HIGH' AND device_id='TH-001'
), occurrences AS (
  SELECT a.id,a.occurrence_kind,a.episode_key,m.wire_alarm_key,
         a.triggered_at::text,a.cleared_at::text,
         a.payload_json->>'org_code' AS org_code,
         a.payload_json->>'occurrence_seq' AS occurrence_seq,
         o.delivery_state,o.id AS outbox_id
  FROM alarm_occurrences a
  JOIN alarm_episode_mappings m ON m.internal_episode_key=a.episode_key
  LEFT JOIN alarm_outbox o ON o.alarm_key=a.alarm_key
  WHERE a.rule_code='S1_TEMP_HIGH' AND a.device_id='TH-001'
    AND a.id > $AfterOccurrenceId
  ORDER BY a.id
)
SELECT json_build_object(
  'device',(SELECT row_to_json(d) FROM
      (SELECT device_id,org_code,site_code,zone_code,enabled
       FROM devices WHERE device_id='TH-001') d),
  'rules',(SELECT json_agg(row_to_json(r) ORDER BY rule_code) FROM
      (SELECT rule_code,enabled,config_version,threshold_value,hold_seconds,
              hysteresis_value,clear_hold_seconds
       FROM rule_definitions
       WHERE rule_code IN ('S1_TEMP_HIGH','S1_DEMO_TEMP_HIGH')) r),
  'runtime',(SELECT row_to_json(runtime) FROM runtime),
  'max_occurrence_id',COALESCE((SELECT max(id) FROM alarm_occurrences),0),
  'occurrences',COALESCE((SELECT json_agg(row_to_json(occurrences)) FROM occurrences),'[]'::json),
  'outbox_counts',(SELECT json_object_agg(delivery_state,count) FROM
      (SELECT delivery_state,count(*) AS count FROM alarm_outbox GROUP BY delivery_state) q)
)::text;
"@
    return Invoke-ReadOnlySql -Sql $sql
}

function Write-AcceptanceReport {
    param($Baseline, $Final, [string]$Result, [string]$Reason,
          [datetime]$StartedAt, [datetime]$FinishedAt, [int]$Samples)
    New-Item -ItemType Directory -Force -Path $reportsRoot | Out-Null
    $evidence = $Final | ConvertTo-Json -Depth 8
    $content = @"
# S1 Physical Temperature Alarm Acceptance

- Result: **$Result**
- Reason: $Reason
- Device/Rule: **$DeviceId / S1_TEMP_HIGH**
- Organization: **$Organization**
- Started (Asia/Ho_Chi_Minh): $($StartedAt.ToString('yyyy-MM-dd HH:mm:ss zzz'))
- Finished (Asia/Ho_Chi_Minh): $($FinishedAt.ToString('yyyy-MM-dd HH:mm:ss zzz'))
- Data API samples observed: $Samples
- Baseline occurrence ID: $($Baseline.max_occurrence_id)
- Safety: read-only Data API GET and PostgreSQL SELECT only; Outbox Worker was not started.

## Acceptance rule

PASS requires a new runtime TRIGGERED and CLEARED for TH-001/S1_TEMP_HIGH with
`org_code=hainam`, exactly one of each, the same internal `episode_key`, the same
`wire_alarm_key`, and corresponding Outbox rows. Local >30/<29 counters are only
operator guidance and are not Rule Engine evidence.

## PostgreSQL evidence

~~~json
$evidence
~~~

## Limitations

The monitor cannot prove how the sensor was heated. Physical handling, safe heat
application, device identity and absence of simulated MQTT publishers remain
operator-observed controls.
"@
    [IO.File]::WriteAllText($reportFullPath, $content, [Text.UTF8Encoding]::new($false))
    Write-Host "Report: $reportFullPath"
}

$started = Get-Date
$samples = 0
$aboveStarted = $null
$belowStarted = $null
$lastSeq = $null
$plainToken = $null
$workerContainer = ((& docker compose ps -q outbox-worker) -join '').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to verify Outbox Worker state' }
if ($workerContainer) { throw 'Outbox Worker is running; physical acceptance monitor refuses to continue' }
$baseline = Get-RuleEvidence
$effectiveBaselineId = if ($BaselineOccurrenceId -ge 0) {
    $BaselineOccurrenceId
} else {
    [long]$baseline.max_occurrence_id
}
$device = $baseline.device
$official = @($baseline.rules | Where-Object rule_code -eq 'S1_TEMP_HIGH')[0]
$demo = @($baseline.rules | Where-Object rule_code -eq 'S1_DEMO_TEMP_HIGH')[0]
if ($device.org_code -ne $Organization -or -not $device.enabled) {
    throw 'TH-001 is not enabled in the requested organization'
}
if (-not $official.enabled -or $official.threshold_value -ne 30 -or
    $official.hold_seconds -ne 300 -or $official.hysteresis_value -ne 1 -or
    $official.clear_hold_seconds -ne 120) {
    throw 'S1_TEMP_HIGH runtime configuration does not match the acceptance contract'
}
if ($demo.enabled) { throw 'S1_DEMO_TEMP_HIGH is enabled; refusing physical acceptance' }
$baseline.max_occurrence_id = $effectiveBaselineId
$final = Get-RuleEvidence -AfterOccurrenceId $effectiveBaselineId

if ($ValidateOnly) {
    $health = Invoke-RestMethod -Uri "$($ApiBaseUrl.TrimEnd('/'))/api/v1/health" -Method Get -TimeoutSec 5
    $reason = "Monitor prerequisites validated; physical heating/cooling sequence was not run. Data API health=$($health.status)."
    Write-AcceptanceReport -Baseline $baseline -Final $final -Result 'FAIL (NOT YET ACCEPTED)' `
        -Reason $reason -StartedAt $started -FinishedAt (Get-Date) -Samples 0
    exit 0
}

if (-not $ApiToken) { $ApiToken = Read-Host 'Data API service token' -AsSecureString }
$plainToken = ConvertFrom-LocalSecureString $ApiToken
$headers = @{ Authorization = "Bearer $plainToken"; 'X-Org-Code' = $Organization }
$deadline = (Get-Date).AddMinutes($DurationMinutes)

try {
    while ((Get-Date) -lt $deadline) {
        $latest = Invoke-RestMethod -Uri "$($ApiBaseUrl.TrimEnd('/'))/api/v1/devices/$DeviceId/latest" `
            -Headers $headers -Method Get -TimeoutSec 5
        $temperature = ($latest.metrics | Where-Object metric -eq 'temperature' | Select-Object -First 1).value
        $humidity = ($latest.metrics | Where-Object metric -eq 'humidity' | Select-Object -First 1).value
        if ($null -eq $temperature) { throw 'Latest response contains no temperature metric' }
        $samples++
        $now = Get-Date
        if ($temperature -gt 30) {
            if ($null -eq $aboveStarted) { $aboveStarted = $now }
        } else { $aboveStarted = $null }
        if ($temperature -lt 29) {
            if ($null -eq $belowStarted) { $belowStarted = $now }
        } else { $belowStarted = $null }
        $aboveSeconds = if ($aboveStarted) { [int]($now - $aboveStarted).TotalSeconds } else { 0 }
        $belowSeconds = if ($belowStarted) { [int]($now - $belowStarted).TotalSeconds } else { 0 }
        $final = Get-RuleEvidence -AfterOccurrenceId $effectiveBaselineId
        $runtime = $final.runtime
        $rulePhase = if ($runtime.is_active) { 'ACTIVE' } elseif ($runtime.hold_started_at) { 'HOLD' } `
                     elseif ($runtime.recovery_started_at) { 'RECOVERY' } else { 'IDLE' }
        $seqMark = if ($null -ne $lastSeq -and $latest.seq -le $lastSeq) { 'SEQ-NOT-ADVANCING' } else { 'SEQ-OK' }
        $lastSeq = $latest.seq
        Write-Host (Format-MonitorLine $latest $temperature $humidity $seqMark `
            $aboveSeconds $belowSeconds $rulePhase @($final.occurrences).Count)
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    $plainToken = $null
    $headers = $null
}

$occurrences = @($final.occurrences)
$triggered = @($occurrences | Where-Object { $_.occurrence_kind -eq 'TRIGGERED' -and $_.org_code -eq $Organization })
$cleared = @($occurrences | Where-Object { $_.occurrence_kind -eq 'CLEARED' -and $_.org_code -eq $Organization })
$pass = $triggered.Count -eq 1 -and $cleared.Count -eq 1 -and
        $triggered[0].episode_key -eq $cleared[0].episode_key -and
        $triggered[0].wire_alarm_key -eq $cleared[0].wire_alarm_key -and
        $triggered[0].outbox_id -and $cleared[0].outbox_id
$result = if ($pass) { 'PASS' } else { 'FAIL (NOT ACCEPTED)' }
$reason = if ($pass) { 'Runtime contains one matching physical TRIGGERED/CLEARED episode and both Outbox rows.' } `
          else { 'Required new hainam TRIGGERED/CLEARED evidence was not complete or was duplicated.' }
Write-AcceptanceReport -Baseline $baseline -Final $final -Result $result -Reason $reason `
    -StartedAt $started -FinishedAt (Get-Date) -Samples $samples
if (-not $pass) { exit 2 }
