[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help','start','status','live','alarms','logs','tunnel','arm','stop-e2e',
                 'data','history','events','raw','export')]
    [string]$Command = 'help',
    [string]$Device,
    [ValidateRange(1, 1000)][int]$Limit = 20,
    [switch]$Follow,
    [switch]$Stream,
    [switch]$Detail,
    [ValidateRange(500, 60000)][int]$IntervalMs = 1000,
    [ValidateSet('temperature','humidity','all')][string]$Metric = 'all',
    [ValidateSet('5m','30m','1h','24h')][string]$Since = '30m',
    [ValidateSet('DOOR_OPENED','DOOR_CLOSED','all')][string]$EventType = 'all',
    [ValidateSet('telemetry','events','snapshots')][string]$Source = 'telemetry',
    [ValidateSet('telemetry','events')][string]$Type = 'telemetry',
    [string]$Start,
    [string]$End,
    [string]$Path,
    [ValidateSet('data-plane','data-api','mqtt','worker')][string]$Service = 'data-plane',
    [ValidateRange(1, 5000)][int]$Tail = 100,
    [ValidateSet('all','ACK','ERROR')][string]$Filter = 'all',
    [string]$Dev1BaseUrl,
    [string]$ConfirmText,
    [switch]$DryRun,
    [switch]$Once
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:Utf8 = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $script:Utf8
$OutputEncoding = $script:Utf8
$env:PGCLIENTENCODING = 'UTF8'
$script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:DbUser = 'nw_user'
$script:DbName = 'nextwireless'
$script:E2EContainer = 'nextwireless-if05-e2e'
$script:VnZone = 'Asia/Ho_Chi_Minh'

function Write-Heading([string]$Text) {
    Write-Host "`n=== $Text ===" -ForegroundColor Cyan
}

function Assert-Tool([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Không tìm thấy '$Name' trong PATH."
    }
}

function Assert-SafeIdentifier([string]$Value, [string]$Name) {
    if ($Value -and $Value -notmatch '^[A-Za-z0-9_.-]+$') {
        throw "$Name không hợp lệ: chỉ cho phép chữ, số, dấu chấm, gạch dưới và gạch ngang."
    }
}

function ConvertTo-SqlLiteral([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

function ConvertTo-UtcSqlLiteral([string]$Value, [string]$Name) {
    if (-not $Value) { return $null }
    try { $parsed = [DateTimeOffset]::Parse($Value) }
    catch { throw "$Name phải là thời gian hợp lệ, ví dụ 2026-11-03T08:00:00+07:00." }
    return ConvertTo-SqlLiteral ($parsed.UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss.ffffff'))
}

function Get-SinceInterval([string]$Value) {
    return @{'5m'='5 minutes';'30m'='30 minutes';'1h'='1 hour';'24h'='24 hours'}[$Value]
}

function Invoke-NwSql([string]$Sql, [switch]$TuplesOnly, [switch]$Csv) {
    Assert-Tool docker
    $args = @('compose','exec','-T','postgres','psql','-X','-v','ON_ERROR_STOP=1',
              '-U',$script:DbUser,'-d',$script:DbName,'-P','pager=off')
    if ($TuplesOnly) { $args += @('-A','-t') }
    if ($Csv) { $args += '--csv' }
    $args += @('-f','-')
    $output = $Sql | & docker @args
    if ($LASTEXITCODE -ne 0) { throw 'Truy vấn PostgreSQL thất bại.' }
    return $output
}

function Test-ContainerRunning([string]$Name) {
    $value = & docker ps --filter "name=^/$Name`$" --format '{{.Names}}'
    return ($LASTEXITCODE -eq 0 -and $value -eq $Name)
}

function Show-Help {
    @'
nextWireless CLI — scripts/nw.ps1

  help       Danh sách lệnh và ví dụ.
  start      Khởi động postgres, mqtt, data-plane, data-api (không profile thử nghiệm).
  status     Trạng thái Compose, ESP32, ngrok và E2E Worker.
  live       Màn hình S1/S6/Alarm; mặc định làm mới 1 giây.
  data       Dữ liệu S1/S6 mới nhất. Hỗ trợ -Device, -Limit, -Follow.
  history    Lịch sử S1. Hỗ trợ -Metric và -Since 5m|30m|1h|24h.
  events     DOOR_OPENED/CLOSED và độ trễ nhận sự kiện.
  raw        JSON gốc từ telemetry report, event hoặc snapshot.
  export     Xuất telemetry/events thành CSV bằng SELECT read-only.
  alarms     Alarm occurrence/cleared và trạng thái Outbox.
  logs       Log service, hỗ trợ -Follow, -Tail, -Filter ACK|ERROR.
  tunnel     Kiểm tra/mở ngrok cho Data API localhost:8002.
  arm        ARM IF-05 cho Alarm mới; có xác nhận, hoặc -DryRun.
  stop-e2e   Chỉ dừng container nextwireless-if05-e2e.

Ví dụ:
  .\scripts\nw.ps1 help
  .\scripts\nw.ps1 start
  .\scripts\nw.ps1 status
  .\scripts\nw.ps1 live -IntervalMs 500
  .\scripts\nw.ps1 live -Stream -Detail
  .\scripts\nw.ps1 data -Device TH-001 -Limit 10
  .\scripts\nw.ps1 data -Device DR-001 -Follow
  .\scripts\nw.ps1 history -Device TH-001 -Metric temperature -Since 1h -Limit 100
  .\scripts\nw.ps1 events -Device DR-001 -EventType DOOR_OPENED -Since 24h
  .\scripts\nw.ps1 raw -Source events -Device DR-001 -Limit 5
  .\scripts\nw.ps1 export -Type telemetry -Device TH-001 -Start 2026-11-03T08:00:00+07:00 -End 2026-11-03T09:00:00+07:00 -Path .\reports\th001.csv
  .\scripts\nw.ps1 alarms -Device DR-001 -Follow
  .\scripts\nw.ps1 logs -Service worker -Follow -Filter ACK
  .\scripts\nw.ps1 tunnel
  .\scripts\nw.ps1 arm -Dev1BaseUrl https://<dev1-host> -DryRun -ConfirmText ARM
  .\scripts\nw.ps1 stop-e2e

`live -IntervalMs 500` chỉ thay đổi tốc độ làm mới màn hình; không thay đổi chu kỳ
ESP32 gửi dữ liệu.
'live -Stream -Detail' thêm một khối mỗi chu kỳ và không xóa lịch sử terminal.
'@ | Write-Host
}

function Start-Core {
    Assert-Tool docker
    Write-Heading 'Khởi động service cốt lõi'
    & docker compose up -d postgres mqtt data-plane data-api
    if ($LASTEXITCODE -ne 0) { throw 'Không thể khởi động service cốt lõi.' }
    Write-Host 'Không khởi động simulator, mock-nextgrp hoặc outbox-worker.' -ForegroundColor Green
}

function Show-Status {
    Write-Heading 'Docker Compose'
    & docker compose ps -a postgres mqtt data-plane data-api snapshot-worker
    if ($LASTEXITCODE -ne 0) { throw 'Không đọc được trạng thái Docker Compose.' }

    Write-Heading 'ESP32 / Data Plane'
    $sql = @"
SELECT d.device_id AS "Thiết bị",d.org_code AS "Org",d.site_code AS "Site",
       COALESCE(s.current_state,'unknown') AS "Trạng thái",
       to_char(s.last_seen_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Lần cuối (VN)",
       CASE WHEN s.last_seen_at IS NULL THEN NULL
            ELSE round(extract(epoch FROM ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-s.last_seen_at))::numeric,1) END AS "Tuổi (s)",
       CASE WHEN s.current_state='online' AND s.last_seen_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-make_interval(secs=>s.timeout_seconds)
            THEN 'ONLINE' WHEN s.current_state='offline' THEN 'OFFLINE' ELSE 'UNKNOWN' END AS "Đánh giá"
FROM devices d LEFT JOIN device_status s USING(device_id)
WHERE d.device_id IN ('TH-001','DR-001') ORDER BY d.device_id;
"@
    Invoke-NwSql $sql | Write-Host

    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8002/api/v1/health' -TimeoutSec 3
        Write-Host "Data API: HTTP 200 / $($health.status) / database=$($health.database)" -ForegroundColor Green
    } catch { Write-Host "Data API: UNKNOWN ($($_.Exception.Message))" -ForegroundColor Yellow }

    Write-Heading 'E2E Worker / ngrok'
    if (Test-ContainerRunning $script:E2EContainer) {
        Write-Host "E2E Worker: RUNNING ($script:E2EContainer)" -ForegroundColor Yellow
    } else { Write-Host 'E2E Worker: STOPPED' -ForegroundColor Green }
    try {
        $tunnels = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 2
        $urls = @($tunnels.tunnels | ForEach-Object public_url)
        if ($urls.Count) { $urls | ForEach-Object { Write-Host "ngrok: $_" } }
        else { Write-Host 'ngrok: UNKNOWN/không có tunnel' }
    } catch { Write-Host 'ngrok: STOPPED hoặc API 4040 không truy cập được' }
}

function Get-S1DataSql([string]$DeviceId, [int]$RowLimit) {
    $dev = ConvertTo-SqlLiteral $DeviceId
    return @"
WITH reports AS (
  SELECT device_id,boot_id,seq,measured_at,received_at,quality,ts_suspect
  FROM telemetry_reports WHERE device_id=$dev
  ORDER BY received_at DESC,id DESC LIMIT $RowLimit
)
SELECT r.device_id AS "Thiết bị",r.seq AS "Seq",
       round(max(t.value_number) FILTER (WHERE t.metric='temperature')::numeric,2) AS "Nhiệt độ (°C)",
       round(max(t.value_number) FILTER (WHERE t.metric='humidity')::numeric,2) AS "Độ ẩm (%RH)",
       to_char(r.measured_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Đo lúc (VN)",
       to_char(r.received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Nhận lúc (VN)",
       round(extract(epoch FROM ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-r.received_at))::numeric,1) AS "Tuổi data (s)",
       CASE WHEN s.current_state='online' AND s.last_seen_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-make_interval(secs=>s.timeout_seconds)
            THEN 'ONLINE' WHEN s.current_state='offline' THEN 'OFFLINE' ELSE 'UNKNOWN' END AS "Online",
       r.quality AS "Quality",r.ts_suspect AS "TS suspect",r.boot_id AS "Boot ID"
FROM reports r LEFT JOIN telemetry t
  ON t.device_id=r.device_id AND t.boot_id=r.boot_id AND t.seq=r.seq
LEFT JOIN device_status s ON s.device_id=r.device_id
GROUP BY r.device_id,r.boot_id,r.seq,r.measured_at,r.received_at,r.quality,r.ts_suspect,
         s.current_state,s.last_seen_at,s.timeout_seconds
ORDER BY r.received_at DESC,r.seq DESC;
"@
}

function Get-S6DataSql([string]$DeviceId, [int]$RowLimit) {
    $dev = ConvertTo-SqlLiteral $DeviceId
    return @"
SELECT e.device_id AS "Thiết bị",
       CASE e.event_type WHEN 'DOOR_OPENED' THEN 'OPEN' WHEN 'DOOR_CLOSED' THEN 'CLOSED' END AS "Trạng thái cửa",
       e.event_type AS "Sự kiện",e.seq AS "Seq",e.boot_id AS "Boot ID",
       to_char(e.event_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Xảy ra (VN)",
       to_char(e.received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Nhận lúc (VN)",
       round(extract(epoch FROM (e.received_at-e.event_at))::numeric,3) AS "Độ trễ (s)",
       round(extract(epoch FROM ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-e.received_at))::numeric,1) AS "Tuổi event (s)",
       CASE WHEN s.current_state='online' AND s.last_seen_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-make_interval(secs=>s.timeout_seconds)
            THEN 'ONLINE' WHEN s.current_state='offline' THEN 'OFFLINE' ELSE 'UNKNOWN' END AS "Online",
       e.ts_suspect AS "TS suspect"
FROM device_events e LEFT JOIN device_status s ON s.device_id=e.device_id
WHERE e.device_id=$dev AND e.event_type IN ('DOOR_OPENED','DOOR_CLOSED')
ORDER BY e.event_at DESC,e.id DESC LIMIT $RowLimit;
"@
}

function Show-Data {
    if ($Device) { Assert-SafeIdentifier $Device 'Device' }
    do {
        if ($Follow) { Clear-Host; Write-Host "Làm mới: $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss') (màn hình ${IntervalMs}ms)" -ForegroundColor DarkGray }
        if (-not $Device -or $Device -eq 'TH-001') {
            Write-Heading 'S1 — telemetry mới nhất'
            $s1Device = if ($Device) { $Device } else { 'TH-001' }
            Invoke-NwSql (Get-S1DataSql $s1Device $Limit) | Write-Host
        }
        if (-not $Device -or $Device -eq 'DR-001') {
            Write-Heading 'S6 — trạng thái theo device_events mới nhất'
            $s6Device = if ($Device) { $Device } else { 'DR-001' }
            Invoke-NwSql (Get-S6DataSql $s6Device $Limit) | Write-Host
        }
        if ($Device -and $Device -notin @('TH-001','DR-001')) {
            Write-Heading 'Telemetry report'
            Invoke-NwSql (Get-S1DataSql $Device $Limit) | Write-Host
            Write-Heading 'Door event'
            Invoke-NwSql (Get-S6DataSql $Device $Limit) | Write-Host
        }
        if (-not $Follow) { break }
        Start-Sleep -Milliseconds $IntervalMs
    } while ($true)
}

function Show-History {
    $target = if ($Device) { $Device } else { 'TH-001' }
    Assert-SafeIdentifier $target 'Device'
    $dev = ConvertTo-SqlLiteral $target
    $metricFilter = if ($Metric -eq 'all') { '' } else { "AND t.metric=" + (ConvertTo-SqlLiteral $Metric) }
    $interval = Get-SinceInterval $Since
    $sql = @"
SELECT t.device_id AS "Thiết bị",t.metric AS "Metric",
       round(t.value_number::numeric,3) AS "Giá trị",
       CASE t.metric WHEN 'temperature' THEN '°C' WHEN 'humidity' THEN '%RH' ELSE p.unit END AS "Đơn vị",
       r.quality AS "Quality",r.ts_suspect AS "TS suspect",t.seq AS "Seq",
       to_char(t.measured_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Đo lúc (VN)",
       to_char(t.received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Nhận lúc (VN)"
FROM telemetry t
LEFT JOIN telemetry_reports r ON r.device_id=t.device_id AND r.boot_id=t.boot_id AND r.seq=t.seq
LEFT JOIN devices d ON d.device_id=t.device_id
LEFT JOIN device_profile_points p ON p.profile_code=d.profile_code AND p.point_code=t.metric AND p.channel='telemetry'
WHERE t.device_id=$dev $metricFilter
  AND t.measured_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-interval '$interval'
ORDER BY t.measured_at DESC,t.id DESC LIMIT $Limit;
"@
    Write-Heading "Lịch sử $target — $Metric — $Since"
    Invoke-NwSql $sql | Write-Host
}

function Show-Events {
    $target = if ($Device) { $Device } else { 'DR-001' }
    Assert-SafeIdentifier $target 'Device'
    $dev = ConvertTo-SqlLiteral $target
    $eventFilter = if ($EventType -eq 'all') { "AND event_type IN ('DOOR_OPENED','DOOR_CLOSED')" } else { "AND event_type=" + (ConvertTo-SqlLiteral $EventType) }
    $interval = Get-SinceInterval $Since
    $sql = @"
SELECT id AS "ID",device_id AS "Thiết bị",event_type AS "Sự kiện",seq AS "Seq",boot_id AS "Boot ID",
       to_char(event_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Xảy ra (VN)",
       to_char(received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Nhận lúc (VN)",
       round(extract(epoch FROM (received_at-event_at))::numeric,3) AS "Độ trễ (s)",
       ts_source AS "Nguồn giờ",ts_suspect AS "TS suspect"
FROM device_events WHERE device_id=$dev $eventFilter
  AND event_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-interval '$interval'
ORDER BY event_at DESC,id DESC LIMIT $Limit;
"@
    Write-Heading "Sự kiện $target — $EventType — $Since"
    Invoke-NwSql $sql | Write-Host
}

function Show-Raw {
    if ($Device) { Assert-SafeIdentifier $Device 'Device' }
    if ($Limit -gt 100) { throw 'raw giới hạn tối đa 100 bản ghi.' }
    $filter = if ($Device) { 'WHERE device_id=' + (ConvertTo-SqlLiteral $Device) } else { '' }
    switch ($Source) {
        'telemetry' {
            $sql = "SELECT jsonb_pretty(jsonb_build_object('report_id',id,'device_id',device_id,'measured_at_utc',measured_at,'measured_at_vn',measured_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','received_at_utc',received_at,'received_at_vn',received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','quality',quality,'ts_suspect',ts_suspect,'payload',payload_json)) FROM telemetry_reports $filter ORDER BY received_at DESC,id DESC LIMIT $Limit;"
        }
        'events' {
            $sql = "SELECT jsonb_pretty(jsonb_build_object('event_row_id',id,'event_at_utc',event_at,'event_at_vn',event_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','received_at_utc',received_at,'received_at_vn',received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','ts_suspect',ts_suspect,'payload',payload_json)) FROM device_events $filter ORDER BY event_at DESC,id DESC LIMIT $Limit;"
        }
        'snapshots' {
            $sql = "SELECT jsonb_pretty(jsonb_build_object('device_id',device_id,'measured_at_utc',measured_at,'measured_at_vn',measured_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','received_at_utc',received_at,'received_at_vn',received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','ts_suspect',ts_suspect,'payload',payload_json)) FROM device_state_snapshots $filter ORDER BY measured_at DESC,seq DESC LIMIT $Limit;"
        }
    }
    Write-Heading "JSON gốc — $Source"
    Invoke-NwSql $sql -TuplesOnly | Write-Host
}

function Export-Data {
    if (-not $Device -or -not $Path) { throw 'export yêu cầu -Device và -Path.' }
    Assert-SafeIdentifier $Device 'Device'
    $dev = ConvertTo-SqlLiteral $Device
    $startFilter = if ($Start) { ' AND t.measured_at >= ' + (ConvertTo-UtcSqlLiteral $Start 'Start') } else { '' }
    $endFilter = if ($End) { ' AND t.measured_at <= ' + (ConvertTo-UtcSqlLiteral $End 'End') } else { '' }
    if ($Start -and $End -and [DateTimeOffset]::Parse($Start) -gt [DateTimeOffset]::Parse($End)) {
        throw 'Start phải nhỏ hơn hoặc bằng End.'
    }
    if ($Type -eq 'telemetry') {
        $sql = "SELECT t.device_id,t.boot_id,t.seq,t.metric,t.value_number,CASE t.metric WHEN 'temperature' THEN '°C' WHEN 'humidity' THEN '%RH' ELSE p.unit END AS unit,r.quality,t.measured_at AS measured_at_utc,(t.measured_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone') AS measured_at_vn,t.received_at AS received_at_utc,(t.received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone') AS received_at_vn,t.ts_source,t.ts_suspect FROM telemetry t LEFT JOIN telemetry_reports r ON r.device_id=t.device_id AND r.boot_id=t.boot_id AND r.seq=t.seq LEFT JOIN devices d ON d.device_id=t.device_id LEFT JOIN device_profile_points p ON p.profile_code=d.profile_code AND p.point_code=t.metric AND p.channel='telemetry' WHERE t.device_id=$dev$startFilter$endFilter ORDER BY t.measured_at DESC,t.id DESC;"
    } else {
        $startFilter = if ($Start) { ' AND event_at >= ' + (ConvertTo-UtcSqlLiteral $Start 'Start') } else { '' }
        $endFilter = if ($End) { ' AND event_at <= ' + (ConvertTo-UtcSqlLiteral $End 'End') } else { '' }
        $sql = "SELECT id,device_id,event_id,event_type,boot_id,seq,event_at AS event_at_utc,(event_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone') AS event_at_vn,received_at AS received_at_utc,(received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone') AS received_at_vn,extract(epoch FROM (received_at-event_at)) AS receive_latency_seconds,ts_source,ts_suspect,event_source FROM device_events WHERE device_id=$dev$startFilter$endFilter ORDER BY event_at DESC,id DESC;"
    }
    $resolved = [IO.Path]::GetFullPath((Join-Path (Get-Location) $Path))
    $directory = [IO.Path]::GetDirectoryName($resolved)
    if (-not [IO.Directory]::Exists($directory)) { [IO.Directory]::CreateDirectory($directory) | Out-Null }
    $csv = @(Invoke-NwSql $sql -Csv)
    [IO.File]::WriteAllLines($resolved, $csv, [Text.UTF8Encoding]::new($false))
    Write-Host "Đã xuất read-only: $resolved ($($csv.Count) dòng gồm header)" -ForegroundColor Green
}

function Show-Alarms {
    if ($Device) { Assert-SafeIdentifier $Device 'Device' }
    $filterSql = if ($Device) { 'AND a.device_id=' + (ConvertTo-SqlLiteral $Device) } else { "AND a.device_id IN ('TH-001','DR-001')" }
    do {
        if ($Follow) { Clear-Host; Write-Host "Alarm — $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')" -ForegroundColor DarkGray }
        $sql = @"
SELECT a.id AS "Occurrence ID",a.device_id AS "Thiết bị",a.occurrence_kind AS "Loại",
       a.rule_code AS "Rule",a.event_type AS "Event",a.payload_json->>'rule_version' AS "Version",
       to_char(a.triggered_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Thời điểm (VN)",
       o.id AS "Outbox ID",COALESCE(o.delivery_state,'NO_OUTBOX') AS "Gửi",
       o.attempt_count AS "Lần thử",COALESCE(o.ack_id,'') AS "ACK",
       to_char(o.delivered_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Đã gửi (VN)",
       a.episode_key AS "Episode"
FROM alarm_occurrences a LEFT JOIN alarm_outbox o ON o.alarm_key=a.alarm_key
WHERE 1=1 $filterSql ORDER BY a.triggered_at DESC,a.id DESC LIMIT $Limit;
"@
        Invoke-NwSql $sql | Write-Host
        if (-not $Follow) { break }
        Start-Sleep -Milliseconds $IntervalMs
    } while ($true)
}

function Format-NwValue($Value, [string]$Suffix = '') {
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return '--' }
    return "${Value}${Suffix}"
}

function Format-NwBool($Value) {
    if ($null -eq $Value) { return '--' }
    if ([bool]$Value) { return 'true' }
    return 'false'
}

function Write-NwField([string]$Label, $Value, [string]$Suffix = '') {
    Write-Host ('  {0,-20}: {1}' -f $Label,(Format-NwValue $Value $Suffix))
}

function Get-LiveDetailSnapshot {
    $sql = @"
WITH
s1_report AS (
  SELECT device_id,boot_id,seq,measured_at,received_at,quality,ts_suspect
  FROM telemetry_reports WHERE device_id='TH-001'
  ORDER BY received_at DESC,id DESC LIMIT 1
),
s1_values AS (
  SELECT round(max(t.value_number) FILTER (WHERE t.metric='temperature')::numeric,2) AS temperature,
         round(max(t.value_number) FILTER (WHERE t.metric='humidity')::numeric,2) AS humidity
  FROM s1_report r LEFT JOIN telemetry t
    ON t.device_id=r.device_id AND t.boot_id=r.boot_id AND t.seq=r.seq
),
s1_status AS (SELECT * FROM device_status WHERE device_id='TH-001'),
s1_rule AS (SELECT rule_code,enabled,config_version,threshold_value,hold_seconds FROM rule_definitions WHERE rule_code='S1_TEMP_HIGH'),
s1_runtime AS (SELECT is_active,current_state,state_changed_at,episode_key FROM rule_runtime_state WHERE rule_code='S1_TEMP_HIGH' AND device_id='TH-001'),
s1_alarm AS (
  SELECT a.occurrence_kind,o.id AS outbox_id,o.delivery_state,o.ack_id,a.triggered_at
  FROM alarm_occurrences a LEFT JOIN alarm_outbox o ON o.alarm_key=a.alarm_key
  WHERE a.device_id='TH-001' AND a.rule_code='S1_TEMP_HIGH'
  ORDER BY a.triggered_at DESC,a.id DESC LIMIT 1
),
s6_event AS (
  SELECT id,event_type,event_at,received_at,seq,boot_id,ts_suspect
  FROM device_events WHERE device_id='DR-001' AND event_type IN ('DOOR_OPENED','DOOR_CLOSED')
  ORDER BY event_at DESC,id DESC LIMIT 1
),
s6_status AS (SELECT * FROM device_status WHERE device_id='DR-001'),
s6_rule AS (SELECT rule_code,enabled,config_version,hold_seconds FROM rule_definitions WHERE rule_code='S6_DOOR_OPEN'),
s6_runtime AS (SELECT is_active,current_state,state_changed_at,episode_key,episode_started_at FROM rule_runtime_state WHERE rule_code='S6_DOOR_OPEN' AND device_id='DR-001'),
s6_alarm AS (
  SELECT a.occurrence_kind,o.id AS outbox_id,o.delivery_state,o.ack_id,a.triggered_at
  FROM alarm_occurrences a LEFT JOIN alarm_outbox o ON o.alarm_key=a.alarm_key
  WHERE a.device_id='DR-001' AND a.rule_code='S6_DOOR_OPEN'
  ORDER BY a.triggered_at DESC,a.id DESC LIMIT 1
)
SELECT json_build_object(
 'captured_vn',to_char(CURRENT_TIMESTAMP AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS'),
 's1',json_build_object(
   'online',CASE WHEN ss.device_id IS NULL THEN NULL
     WHEN ss.current_state='online' AND ss.last_seen_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-make_interval(secs=>ss.timeout_seconds) THEN 'ONLINE'
     WHEN ss.current_state='offline' THEN 'OFFLINE' ELSE 'UNKNOWN' END,
   'temperature',sv.temperature,'humidity',sv.humidity,'quality',sr.quality,
   'measured_vn',to_char(sr.measured_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS'),
   'received_vn',to_char(sr.received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS'),
   'seq',sr.seq,'boot_id',sr.boot_id,'ts_suspect',sr.ts_suspect,
   'rule_code',s1r.rule_code,'rule_enabled',s1r.enabled,'rule_version',s1r.config_version,
   'threshold',s1r.threshold_value,'hold_seconds',s1r.hold_seconds,
   'runtime_active',s1rt.is_active,'runtime_state',s1rt.current_state,
   'latest_occurrence',s1a.occurrence_kind,'outbox_id',s1a.outbox_id,
   'delivery_state',s1a.delivery_state,'ack_id',s1a.ack_id),
 's6',json_build_object(
   'online',CASE WHEN ds.device_id IS NULL THEN NULL
     WHEN ds.current_state='online' AND ds.last_seen_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-make_interval(secs=>ds.timeout_seconds) THEN 'ONLINE'
     WHEN ds.current_state='offline' THEN 'OFFLINE' ELSE 'UNKNOWN' END,
   'door_state',CASE de.event_type WHEN 'DOOR_OPENED' THEN 'OPEN' WHEN 'DOOR_CLOSED' THEN 'CLOSED' END,
   'event_type',de.event_type,
   'event_vn',to_char(de.event_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS'),
   'received_vn',to_char(de.received_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS'),
   'seq',de.seq,'boot_id',de.boot_id,'ts_suspect',de.ts_suspect,
   'event_open_seconds',CASE WHEN de.event_type='DOOR_OPENED' THEN greatest(0,floor(extract(epoch FROM ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-de.event_at))))::bigint END,
   'rule_code',s6r.rule_code,'rule_enabled',s6r.enabled,'rule_version',s6r.config_version,
   'hold_seconds',s6r.hold_seconds,'runtime_active',s6rt.is_active,
   'runtime_state',s6rt.current_state,
   'runtime_state_vn',to_char(s6rt.state_changed_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS'),
   'rule_timer_start_vn',CASE WHEN s6rt.current_state='open' AND s6rt.episode_started_at IS NOT NULL
     THEN to_char(s6rt.episode_started_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') END,
   'runtime_open_seconds',CASE WHEN s6rt.current_state='open' AND s6rt.episode_started_at IS NOT NULL
     THEN greatest(0,floor(extract(epoch FROM ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')-s6rt.episode_started_at))))::bigint END,
   'latest_occurrence',s6a.occurrence_kind,'outbox_id',s6a.outbox_id,
   'delivery_state',s6a.delivery_state,'ack_id',s6a.ack_id)
)::text
FROM (SELECT 1) base
LEFT JOIN s1_report sr ON true LEFT JOIN s1_values sv ON true LEFT JOIN s1_status ss ON true
LEFT JOIN s1_rule s1r ON true LEFT JOIN s1_runtime s1rt ON true LEFT JOIN s1_alarm s1a ON true
LEFT JOIN s6_event de ON true LEFT JOIN s6_status ds ON true LEFT JOIN s6_rule s6r ON true
LEFT JOIN s6_runtime s6rt ON true LEFT JOIN s6_alarm s6a ON true;
"@
    $json = (@(Invoke-NwSql $sql -TuplesOnly) -join '').Trim()
    if (-not $json) { throw 'PostgreSQL không trả về live detail snapshot.' }
    return $json | ConvertFrom-Json
}

function Get-CurrentAlarmLabel($RuntimeActive, $LatestOccurrence) {
    if ($null -eq $RuntimeActive) { return '--' }
    if ([bool]$RuntimeActive) { return 'TRIGGERED' }
    if ($LatestOccurrence -eq 'CLEARED') { return 'CLEARED' }
    return 'INACTIVE'
}

function Show-LiveDetail {
    do {
        if (-not $Stream) { Clear-Host }
        $snapshot = Get-LiveDetailSnapshot
        $s1 = $snapshot.s1
        $s6 = $snapshot.s6

        Write-Host '========== NEXTWIRELESS LIVE ==========' -ForegroundColor Cyan
        Write-Host (Format-NwValue $snapshot.captured_vn)
        Write-Host ''
        Write-Host '[S1] TH-001' -ForegroundColor Yellow
        Write-NwField 'Trạng thái' $s1.online
        Write-NwField 'Nhiệt độ' $s1.temperature ' °C'
        Write-NwField 'Độ ẩm' $s1.humidity ' %RH'
        Write-NwField 'Quality' $s1.quality
        Write-NwField 'Thời gian đo' $s1.measured_vn
        Write-NwField 'Thời gian nhận' $s1.received_vn
        Write-NwField 'Sequence' $s1.seq
        Write-NwField 'Boot ID' $s1.boot_id
        Write-NwField 'ts_suspect' (Format-NwBool $s1.ts_suspect)
        $s1Rule = if ($s1.rule_code) { "$($s1.rule_code) v$($s1.rule_version) (> $($s1.threshold) °C, hold $($s1.hold_seconds)s)" } else { '--' }
        Write-NwField 'Rule' $s1Rule
        Write-NwField 'Alarm hiện tại' (Get-CurrentAlarmLabel $s1.runtime_active $s1.latest_occurrence)
        Write-NwField 'Occurrence cuối' $s1.latest_occurrence
        Write-NwField 'Outbox ID' $s1.outbox_id
        Write-NwField 'Gửi Dev 1' $s1.delivery_state
        Write-NwField 'ACK Dev 1' $s1.ack_id

        Write-Host ''
        Write-Host '[S6] DR-001' -ForegroundColor Yellow
        Write-NwField 'Trạng thái' $s6.online
        Write-NwField 'Cửa' $s6.door_state
        Write-NwField 'Event' $s6.event_type
        Write-NwField 'Quality' '--'
        Write-NwField 'Thời gian event (thiết bị)' $s6.event_vn
        Write-NwField 'Thời gian nhận' $s6.received_vn
        Write-NwField 'Sequence' $s6.seq
        Write-NwField 'Boot ID' $s6.boot_id
        Write-NwField 'ts_suspect' (Format-NwBool $s6.ts_suspect)
        $s6Rule = if ($s6.rule_code) { "$($s6.rule_code) v$($s6.rule_version) (hold $($s6.hold_seconds)s)" } else { '--' }
        Write-NwField 'Rule' $s6Rule
        if ($s6.door_state -eq 'OPEN') {
            $eventDuration = if ($null -ne $s6.event_open_seconds) { "$($s6.event_open_seconds) / $($s6.hold_seconds) giây (ước tính từ event)" } else { '--' }
            Write-NwField 'Ước tính từ event thiết bị' $eventDuration
        } else { Write-NwField 'Ước tính từ event thiết bị' '--' }
        $runtimeDetail = if ($null -eq $s6.runtime_state) { '--' }
            elseif ($s6.runtime_state -eq 'open' -and $null -ne $s6.runtime_open_seconds) { "OPEN $($s6.runtime_open_seconds) / $($s6.hold_seconds) giây (clock server); active=$(Format-NwBool $s6.runtime_active)" }
            elseif ($s6.runtime_state -eq 'open') { "OPEN; timer server chưa có dữ liệu; active=$(Format-NwBool $s6.runtime_active)" }
            else { "$($s6.runtime_state.ToUpper()) từ $($s6.runtime_state_vn); active=$(Format-NwBool $s6.runtime_active)" }
        $timerStart = if ($s6.runtime_state -eq 'open' -and $s6.rule_timer_start_vn) { $s6.rule_timer_start_vn } else { '--' }
        Write-NwField 'Timer server bắt đầu' $timerStart
        Write-NwField 'Rule Engine xác nhận' $runtimeDetail
        Write-NwField 'Alarm hiện tại' (Get-CurrentAlarmLabel $s6.runtime_active $s6.latest_occurrence)
        Write-NwField 'Occurrence cuối' $s6.latest_occurrence
        Write-NwField 'Outbox ID' $s6.outbox_id
        Write-NwField 'Gửi Dev 1' $s6.delivery_state
        Write-NwField 'ACK Dev 1' $s6.ack_id
        Write-Host ''
        Write-Host '---------------------------------------' -ForegroundColor DarkGray
        Write-Host ''

        if ($Once) { break }
        Start-Sleep -Milliseconds $IntervalMs
    } while ($true)
}

function Show-Live {
    do {
        Clear-Host
        Write-Host "nextWireless LIVE — $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss') VN" -ForegroundColor Cyan
        Write-Host "Refresh ${IntervalMs}ms; chu kỳ ESP32 độc lập với tốc độ màn hình." -ForegroundColor DarkGray
        Invoke-NwSql (Get-S1DataSql 'TH-001' 1) | Write-Host
        # Door state is authoritative from the newest transition event, never an unordered snapshot.
        Invoke-NwSql (Get-S6DataSql 'DR-001' 1) | Write-Host
        $sql = @"
SELECT a.device_id AS "Thiết bị",a.occurrence_kind AS "Alarm",a.rule_code AS "Rule",
       COALESCE(o.delivery_state,'NO_OUTBOX') AS "Outbox",
       to_char(a.triggered_at AT TIME ZONE 'UTC' AT TIME ZONE '$script:VnZone','DD/MM/YYYY HH24:MI:SS') AS "Thời điểm (VN)"
FROM alarm_occurrences a LEFT JOIN alarm_outbox o ON o.alarm_key=a.alarm_key
WHERE a.device_id IN ('TH-001','DR-001')
ORDER BY a.triggered_at DESC,a.id DESC LIMIT 4;
"@
        Invoke-NwSql $sql | Write-Host
        if ($Once) { break }
        Start-Sleep -Milliseconds $IntervalMs
    } while ($true)
}

function Show-Logs {
    $serviceMap = @{'worker'='outbox-worker';'data-plane'='data-plane';'data-api'='data-api';'mqtt'='mqtt'}
    $target = $serviceMap[$Service]
    if ($Service -eq 'worker' -and (Test-ContainerRunning $script:E2EContainer)) {
        $args = @('logs','--timestamps','--tail',"$Tail")
        if ($Follow) { $args += '--follow' }
        $args += $script:E2EContainer
    } else {
        $args = @('compose','logs','--timestamps','--tail',"$Tail")
        if ($Follow) { $args += '--follow' }
        $args += $target
    }
    & docker @args 2>&1 | ForEach-Object {
        $line = [string]$_
        $show = ($Filter -eq 'all') -or
                ($Filter -eq 'ACK' -and $line -match '(?i)ACK|DELIVERED') -or
                ($Filter -eq 'ERROR' -and $line -match '(?i)ERROR|FAILED|Traceback|Exception')
        if ($show) {
            $line = $line -replace '(?i)(Authorization|API[_ -]?KEY|API[_ -]?SECRET|TOKEN|PASSWORD)\s*[:=]\s*\S+', '$1=[REDACTED]'
            if ($line -match '^(?<prefix>.*?\|\s*)?(?<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)(?<rest>.*)$') {
                $prefixText = if ($Matches.ContainsKey('prefix')) { $Matches['prefix'] } else { '' }
                $timestampText = $Matches['ts']
                $restText = $Matches['rest']
                $vn = ([DateTimeOffset]::Parse($timestampText)).ToOffset([TimeSpan]::FromHours(7)).ToString('dd/MM/yyyy HH:mm:ss.fff')
                Write-Host "$prefixText$vn VN$restText"
            } else { Write-Host $line }
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Không đọc được log service $target." }
}

function Open-Tunnel {
    try { $health = Invoke-WebRequest 'http://127.0.0.1:8002/api/v1/health' -UseBasicParsing -TimeoutSec 5 }
    catch { throw 'Data API localhost:8002 chưa sẵn sàng; không mở tunnel.' }
    Write-Host "Data API health HTTP $($health.StatusCode)" -ForegroundColor Green
    try {
        $existing = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 2
        $url = @($existing.tunnels | Where-Object { $_.config.addr -match ':8002$|localhost:8002$' } | Select-Object -First 1).public_url
        if ($url) { Write-Host "Tunnel hiện tại: $url" -ForegroundColor Green; return }
    } catch { }
    Assert-Tool ngrok
    Start-Process -FilePath (Get-Command ngrok).Source -ArgumentList @('http','8002') -WindowStyle Hidden
    Start-Sleep -Seconds 2
    try {
        $created = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 5
        $url = @($created.tunnels | Where-Object { $_.config.addr -match ':8002$|localhost:8002$' } | Select-Object -First 1).public_url
        if (-not $url) { throw 'không tìm thấy URL' }
        Write-Host "Tunnel Data API: $url" -ForegroundColor Green
        Write-Host 'Cung cấp URL này cho Dev 1 nếu Dashboard cần đọc Data API.' -ForegroundColor Yellow
    } catch { throw "ngrok đã chạy nhưng chưa đọc được URL: $($_.Exception.Message)" }
}

function Get-Dev1Url {
    $base = if ($Dev1BaseUrl) { $Dev1BaseUrl } elseif ($env:DEV1_BASE_URL) { $env:DEV1_BASE_URL } else { '' }
    if (-not $base) { throw 'arm yêu cầu -Dev1BaseUrl hoặc biến môi trường DEV1_BASE_URL.' }
    try { $uri = [Uri]$base }
    catch { throw 'Dev1BaseUrl không hợp lệ.' }
    if ($uri.Scheme -ne 'https') { throw 'Dev1BaseUrl phải dùng HTTPS.' }
    return $base.TrimEnd('/')
}

function Arm-E2E {
    if ($ConfirmText -ne 'ARM') { throw 'Từ chối ARM. Chạy lại với -ConfirmText ARM sau khi operator phê duyệt.' }
    if (Test-ContainerRunning $script:E2EContainer) { throw "$script:E2EContainer đã chạy; không mở worker trùng." }
    $workers = @(& docker ps --filter 'label=com.docker.compose.service=outbox-worker' --format '{{.Names}}')
    if ($workers.Count -gt 0 -and $workers[0]) { throw "Đã có Outbox Worker chạy: $($workers -join ', ')" }
    $base = Get-Dev1Url
    try { $ping = Invoke-WebRequest "$base/api/method/ping" -UseBasicParsing -Headers @{'ngrok-skip-browser-warning'='true'} -TimeoutSec 10 }
    catch { throw "Không kết nối được Dev 1 ping: $($_.Exception.Message)" }
    if ($ping.StatusCode -ne 200) { throw "Dev 1 ping trả HTTP $($ping.StatusCode)." }

    $mappingSql = @"
SELECT
 (SELECT count(*) FROM integration_identity_mappings
  WHERE system_code='nextgrp' AND confirmed=TRUE AND enabled=TRUE
    AND ((entity_type='ORGANIZATION' AND internal_id='hainam')
      OR (entity_type='DEVICE' AND internal_id IN ('TH-001','DR-001'))
      OR (entity_type='RULE' AND internal_id IN ('S1_TEMP_HIGH','S6_DOOR_OPEN') AND config_version=2)))::text
 || '|' ||
 (SELECT count(*) FROM rule_definitions
  WHERE rule_code IN ('S1_TEMP_HIGH','S6_DOOR_OPEN') AND config_version=2
    AND enabled=TRUE AND scope_type='DEVICE'
    AND ((rule_code='S1_TEMP_HIGH' AND device_id='TH-001')
      OR (rule_code='S6_DOOR_OPEN' AND device_id='DR-001')))::text;
"@
    $preflightCounts = (@(Invoke-NwSql $mappingSql -TuplesOnly) -join '').Trim().Split('|')
    $mappingActual = if ($preflightCounts.Count -ge 1) { $preflightCounts[0] } else { '?' }
    $ruleActual = if ($preflightCounts.Count -ge 2) { $preflightCounts[1] } else { '?' }
    if ($preflightCounts.Count -ne 2 -or $mappingActual -ne '5' -or $ruleActual -ne '2') {
        throw "Preflight fail-closed: mapping=$mappingActual/5, Rule v2 enabled=$ruleActual/2."
    }

    # One read-only statement captures both boundaries from the same database instant.
    $markSql = "SELECT COALESCE(max(id),0)::text || '|' || extract(epoch FROM clock_timestamp())::text FROM alarm_outbox;"
    $mark = (@(Invoke-NwSql $markSql -TuplesOnly) -join '').Trim().Split('|')
    if ($mark.Count -ne 2) { throw 'Không parse được high-water mark.' }
    $highWater = [long]$mark[0]
    $epochSeconds = [decimal]::Parse($mark[1],[Globalization.CultureInfo]::InvariantCulture)
    $armedAt = [DateTimeOffset]::FromUnixTimeMilliseconds([long]($epochSeconds * 1000)).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    $armedAtVn = ([DateTimeOffset]::Parse($armedAt)).ToOffset([TimeSpan]::FromHours(7)).ToString('dd/MM/yyyy HH:mm:ss')
    Write-Host "Preflight PASS: Dev1 HTTP 200; high-water=$highWater; thời điểm ARM=$armedAtVn VN" -ForegroundColor Green
    if ($DryRun) { Write-Host 'DRY-RUN: không đọc credential, không tạo worker, không POST.' -ForegroundColor Yellow; return }

    $secure = Read-Host 'Nhập Frappe API KEY:SECRET' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $tokenPlain = $null
    $headers = $null
    try {
        $tokenPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        $headers = @{'Authorization'="token $tokenPlain";'X-Frappe-Site-Name'='nextgrp.localhost';'ngrok-skip-browser-warning'='true'}
        $configUrl = "$base/api/method/nextgrp.nextgrp_iot.controller.config_controller.get_device_config?device_id=NW-IT-TH001"
        try { $authCheck = Invoke-WebRequest $configUrl -UseBasicParsing -Headers $headers -TimeoutSec 10 }
        catch { throw "Dev 1 authenticated GET Config thất bại: $($_.Exception.Message)" }
        if ($authCheck.StatusCode -ne 200) { throw "Dev 1 authenticated GET Config trả HTTP $($authCheck.StatusCode)." }
        $env:BRIDGE_OUTBOUND_TOKEN = $tokenPlain
        $alarmUrl = "$base/api/method/nextgrp.nextgrp_iot.controller.alarm_controller.receive_alarm"
        $runArgs = @('compose','--profile','outbound','run','--no-deps','--rm','-d',
          '--name',$script:E2EContainer,
          '-e','OUTBOX_DELIVERY_ENABLED=true','-e','IF05_E2E_ARMED=true',
          '-e',"IF05_E2E_HIGH_WATER_ID=$highWater",'-e',"IF05_E2E_ARMED_AT=$armedAt",
          '-e','IF05_E2E_ORG=hainam','-e','IF05_E2E_DEVICES=TH-001,DR-001',
          '-e','IF05_E2E_RULES=S1_TEMP_HIGH,S6_DOOR_OPEN','-e','BRIDGE_MODE=real',
          '-e','BRIDGE_ALARM_PROTOCOL=frappe','-e','BRIDGE_OUTBOUND_TOKEN',
          '-e',"BRIDGE_ALARM_URL=$alarmUrl",'-e',"BRIDGE_STATUS_URL=$alarmUrl",
          '-e','FRAPPE_SITE_NAME=nextgrp.localhost','-e','BRIDGE_ALLOW_INSECURE_HTTP=false',
          '-e','BRIDGE_REQUIRE_202=false','outbox-worker')
        & docker @runArgs | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Không thể tạo E2E Worker.' }
        if (-not (Test-ContainerRunning $script:E2EContainer)) { throw 'E2E Worker không ở trạng thái running.' }
        Write-Host "ARMED: $script:E2EContainer; chỉ Outbox ID > $highWater và tạo sau $armedAtVn VN." -ForegroundColor Yellow
    } finally {
        $tokenPlain = $null
        $headers = $null
        if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        Remove-Item Env:BRIDGE_OUTBOUND_TOKEN -ErrorAction SilentlyContinue
    }
}

function Stop-E2E {
    if (-not (Test-ContainerRunning $script:E2EContainer)) {
        Write-Host 'E2E Worker không chạy; không thay đổi service nào.' -ForegroundColor Green
        return
    }
    if ($DryRun) { Write-Host "DRY-RUN: sẽ chỉ dừng $script:E2EContainer." -ForegroundColor Yellow; return }
    & docker stop $script:E2EContainer | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Không thể dừng $script:E2EContainer." }
    Remove-Item Env:BRIDGE_OUTBOUND_TOKEN -ErrorAction SilentlyContinue
    Write-Host "Đã dừng riêng $script:E2EContainer. Core services và backlog không bị thay đổi." -ForegroundColor Green
}

Push-Location $script:Root
try {
    switch ($Command) {
        'help'     { Show-Help }
        'start'    { Start-Core }
        'status'   { Show-Status }
        'live'     {
            if ($Detail) { Show-LiveDetail }
            elseif ($Stream) { throw '-Stream yêu cầu dùng cùng -Detail.' }
            else { Show-Live }
        }
        'data'     { Show-Data }
        'history'  { Show-History }
        'events'   { Show-Events }
        'raw'      { Show-Raw }
        'export'   { Export-Data }
        'alarms'   { Show-Alarms }
        'logs'     { Show-Logs }
        'tunnel'   { Open-Tunnel }
        'arm'      { Arm-E2E }
        'stop-e2e' { Stop-E2E }
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
} finally {
    Pop-Location
}
