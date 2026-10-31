# nextWireless M1 - FIXED 2026-10-30: device_status and telemetry_reports schema
# ISOLATED staging only: Docker project nextwireless-dev2-staging,
# PostgreSQL nextwireless_staging, API port 8003; Dev1 Frappe test API.
# No MQTT, no physical ESP32, no original runtime services touched.
# Load with: . .\nextwireless_m1_demo_staging.ps1
# Then: Initialize-NWDemo; Invoke-NWDemoStep 1 ... Invoke-NWDemoStep 10

$script:NWDemo = $null
$script:NWOrg = 'NW-LOCAL-ORG-001'
$script:NWSite = 'NW-LOCAL-SITE-001'
$script:NWS1 = 'NW-IT-TH001'
$script:NWS6 = 'NW-IT-DR001'
$script:NWDP = 'https://sequence-precision-bubbly.ngrok-free.dev'
$script:NWDev1 = 'https://semisweet-conceal-gizzard.ngrok-free.dev'
$script:NWSiteHeader = 'nextgrp.localhost'

function Get-NWTimeUtc { ([datetimeoffset]::UtcNow).ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
function New-NWId { [guid]::NewGuid().ToString('N') }
function Invoke-NWStageSql([string]$Sql) {
    if (-not (Test-Path '.secrets/staging.env')) { throw 'Run inside E:\nextwireless-dev2-postgres-timescale-prep' }
    $Sql | docker compose --env-file .secrets/staging.env -p nextwireless-dev2-staging -f compose.staging.yaml exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 -U $POSTGRES_USER -d $POSTGRES_DB'
    if ($LASTEXITCODE -ne 0) { throw 'SQL failed. Transaction rolled back. Do not proceed.' }
}
function Send-NWDev1Alarm([hashtable]$Payload) {
    $resp = Invoke-RestMethod -Method POST -Uri "$script:NWDev1/api/method/nextgrp.nextgrp_iot.controller.alarm_controller.receive_alarm" -Headers $script:NWDemo.FrappeHeaders -ContentType 'application/x-www-form-urlencoded; charset=utf-8' -Body @{payload=($Payload | ConvertTo-Json -Depth 20 -Compress)} -ErrorAction Stop -TimeoutSec 20
    if ($null -eq $resp.message) { throw 'Dev1 alarm endpoint did not return a business ACK (message)' }
    if ([string]$resp.message.alarm_key -ne [string]$Payload.alarm_key) { throw 'Dev1 ACK alarm_key mismatch' }
    if ($resp.message.duplicate -eq $true) { throw 'Dev1 returned duplicate. Verify Inbox before continuing.' }
    if ($null -ne $resp.message.occurrence_count -and [int]$resp.message.occurrence_count -ne [int]$Payload.occurrence_seq) {
        throw "ACK occurrence_count=$($resp.message.occurrence_count), expected $($Payload.occurrence_seq); stop and inspect Dev1 Inbox"
    }
    Write-Host "ALARM ACK: $($resp.message.alarm_key) count=$($resp.message.occurrence_count) status=$($resp.message.status)" -ForegroundColor Green
    return $resp.message
}
function Send-NWDev1Status([string]$Device,[ValidateSet('online','offline')][string]$State) {
    $r = Invoke-RestMethod -Method POST -Uri "$script:NWDev1/api/method/nextgrp.nextgrp_iot.controller.status_controller.receive_status" -Headers $script:NWDemo.FrappeHeaders -ContentType 'application/x-www-form-urlencoded; charset=utf-8' -Body @{device_id=$Device;state=$State} -ErrorAction Stop -TimeoutSec 20
    if ($null -eq $r.message) { throw 'Dev1 status endpoint missing business ACK' }
    Write-Host "DEV1 STATUS ACK: $Device -> $State" -ForegroundColor Green
    return $r.message
}
function Get-NWConfig([string]$Device) {
    $url = "$script:NWDev1/api/method/nextgrp.nextgrp_iot.controller.config_controller.get_device_config?device_id=$([uri]::EscapeDataString($Device))"
    $r = Invoke-RestMethod -Uri $url -Headers $script:NWDemo.FrappeHeaders -ErrorAction Stop -TimeoutSec 20
    if ($null -eq $r.message -or [string]$r.message.device.device_id -ne $Device) { throw "Device config mismatch: $Device" }
    if ([string]$r.message.site.organization -ne $script:NWOrg) { throw "Org mismatch: $Device" }
    if ([string]$r.message.device.site -ne $script:NWSite) { throw "Site mismatch: $Device" }
    return $r.message
}
function Initialize-NWDemo {
    if ((Get-Location).Path -ne 'E:\nextwireless-dev2-postgres-timescale-prep') {
        Write-Host 'Make sure you are in E:\nextwireless-dev2-postgres-timescale-prep' -ForegroundColor Yellow
    }
    $line = Get-Content '.secrets/staging.env' | Where-Object { $_ -match '^STAGING_DP_INBOUND_TOKEN=' } | Select-Object -First 1
    if (-not $line) { throw 'No staging Bearer token in .secrets/staging.env' }
    $dpToken = ($line -split '=',2)[1].Trim().Trim('"').Trim("'")
    $secure = Read-Host 'Dev1 Frappe API_KEY:API_SECRET (hidden)' -AsSecureString
    $pair = [System.Net.NetworkCredential]::new('', $secure).Password
    $secure.Dispose()
    if ($pair -notmatch '^[^:\s]+:[^:\s]+$') { throw 'Expected full KEY:SECRET' }
    $script:NWDemo = @{
        DPHeaders = @{Authorization="Bearer $dpToken"; 'X-Org-Code'=$script:NWOrg; 'ngrok-skip-browser-warning'='true'}
        FrappeHeaders = @{Authorization="token $pair"; 'X-Frappe-Site-Name'=$script:NWSiteHeader; 'ngrok-skip-browser-warning'='true';Accept='application/json'}
        TemperatureAlarmKey = $null; TemperatureAlarmStart = $null; TemperatureCount = 0
        DoorAlarmKey = $null; DoorAlarmStart = $null; DoorCount = 0
        Threshold = $null; S1Rule=$null; S6Rule=$null; S1Config=$null; S6Config=$null
        TelemetrySeq=0; TelemetryBoot=((New-NWId).Substring(0,8))
    }
    $pair = $null; $dpToken = $null
    $configS1 = Get-NWConfig $script:NWS1
    $configS6 = Get-NWConfig $script:NWS6
    $s1rules = @($configS1.rules | Where-Object { $_ -and [string]$_.rule_type -eq 'THRESHOLD' -and [string]$_.data_point -eq 'temperature' -and ([string]$_.active -in @('1','True','true')) })
    if ($s1rules.Count -ne 1) { throw "Expected exactly one active temperature THRESHOLD rule; got $($s1rules.Count)" }
    if ($null -eq $s1rules[0].threshold) { throw 'S1 Rule missing threshold' }
    $s6rules = @($configS6.rules | Where-Object { $_ -and [string]$_.rule_id -eq 'NW-IT-RULE-S6' })
    if ($s6rules.Count -ne 1) { throw 'S6 Rule NW-IT-RULE-S6 not confirmed in Dev1 config; stop and verify rule_id' }
    $script:NWDemo.S1Rule = $s1rules[0]
    $script:NWDemo.S6Rule = $s6rules[0]
    $script:NWDemo.Threshold = [double]$s1rules[0].threshold
    $script:NWDemo.S1Config = $configS1
    $script:NWDemo.S6Config = $configS6
    # Read-only staging DB guard, before any writes.
    @'
DO $guard$ BEGIN
  IF current_database() <> 'nextwireless_staging' THEN RAISE EXCEPTION 'NOT STAGING'; END IF;
  IF (SELECT COUNT(*) FROM devices WHERE org_code = 'NW-LOCAL-ORG-001' AND device_id IN ('NW-IT-TH001','NW-IT-DR001')) <> 2
  THEN RAISE EXCEPTION 'Missing or wrong staging devices'; END IF;
END $guard$;
'@ | ForEach-Object { Invoke-NWStageSql $_ }
    Write-Host "READY | S1 threshold: $($script:NWDemo.Threshold) C | Rule S1: $($script:NWDemo.S1Rule.rule_id) | Rule S6: $($script:NWDemo.S6Rule.rule_id)" -ForegroundColor Green
    Write-Host 'Synthetic test only: this does NOT exercise MQTT or automatic Rule Engine evaluation.' -ForegroundColor Yellow
}
function Assert-NWDemoReady {
    if ($null -eq $script:NWDemo -or $null -eq $script:NWDemo.S1Rule) { throw 'Run Initialize-NWDemo first' }
}
function Set-NWStageConnection([ValidateSet('online','offline')][string]$State,[string]$Device) {
    Assert-NWDemoReady
    if ($Device -notin @('NW-IT-TH001','NW-IT-DR001')) { throw 'Only 2 staging demo devices allowed' }
    # Data API reads device_status, NOT devices. Preserve last_seen_at on offline.
    $boot = if ($Device -eq $script:NWS1) { $script:NWDemo.TelemetryBoot } else { 'a1b2c3d4' }
    $sql = @'
DO $status$
DECLARE
  v_device text := '__DEVICE__';
  v_state text := '__STATE__';
  v_boot text := '__BOOT__';
  v_time timestamp := clock_timestamp() AT TIME ZONE 'UTC';
  v_previous varchar(8);
BEGIN
  IF current_database() <> 'nextwireless_staging' THEN RAISE EXCEPTION 'NOT STAGING'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM devices
    WHERE device_id=v_device AND org_code='NW-LOCAL-ORG-001' AND enabled=TRUE
  ) THEN RAISE EXCEPTION 'Staging device missing/disabled'; END IF;
  SELECT current_state INTO v_previous FROM device_status
    WHERE device_id=v_device FOR UPDATE;
  INSERT INTO device_status (device_id,current_state,received_at,boot_id,last_seen_at)
  VALUES (v_device,v_state,v_time,v_boot,CASE WHEN v_state='online' THEN v_time ELSE NULL END)
  ON CONFLICT (device_id) DO UPDATE SET
    current_state=EXCLUDED.current_state,
    received_at=EXCLUDED.received_at,
    boot_id=EXCLUDED.boot_id,
    last_seen_at=CASE WHEN EXCLUDED.current_state='online'
                      THEN EXCLUDED.received_at ELSE device_status.last_seen_at END;
  IF v_previous IS DISTINCT FROM v_state THEN
    INSERT INTO device_status_history
      (device_id,state,received_at,boot_id,previous_state,reason)
    VALUES (v_device,v_state,v_time,v_boot,v_previous,
            CASE WHEN v_state='online' THEN 'demo-connect' ELSE 'demo-offline' END);
  END IF;
  RAISE NOTICE 'STAGING STATUS: % -> % at % UTC', v_device,v_state,v_time;
END $status$;
'@
    Invoke-NWStageSql ($sql.Replace('__STATE__',$State).Replace('__DEVICE__',$Device).Replace('__BOOT__',$boot))
}
function Add-NWStageMeasurement([double]$Temperature,[double]$Humidity) {
    Assert-NWDemoReady
    $t = $Temperature.ToString('0.00',[cultureinfo]::InvariantCulture)
    $h = $Humidity.ToString('0.00',[cultureinfo]::InvariantCulture)
    $script:NWDemo.TelemetrySeq++
    $boot = $script:NWDemo.TelemetryBoot
    $seq = [string]$script:NWDemo.TelemetrySeq
    # Data API /latest requires telemetry_reports AND 2 matching telemetry rows.
    # Staging-only inserts, one transaction; no MQTT/Rule Engine is executed.
    $sql = @'
DO $telemetry$
DECLARE
  v_device text := 'NW-IT-TH001';
  v_boot text := '__BOOT__';
  v_seq bigint := __SEQ__;
  v_ts timestamp := clock_timestamp() AT TIME ZONE 'UTC';
  v_payload jsonb;
BEGIN
  IF current_database() <> 'nextwireless_staging' THEN RAISE EXCEPTION 'NOT STAGING'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM devices WHERE device_id=v_device
    AND org_code='NW-LOCAL-ORG-001' AND enabled=TRUE
  ) THEN RAISE EXCEPTION 'S1 staging device missing/disabled/profile mismatch'; END IF;
  IF to_regclass('public.telemetry') IS NULL OR to_regclass('public.telemetry_reports') IS NULL
  THEN RAISE EXCEPTION 'Required telemetry tables missing'; END IF;
  v_payload := jsonb_build_object(
    'schema_version',1,'device_id',v_device,'boot_id',v_boot,'seq',v_seq,
    'ts',to_char(v_ts,'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'ts_source','ntp','quality','good','msg_kind','measurement',
    'data',jsonb_build_object('temperature',__TEMP__,'humidity',__HUM__)
  );
  INSERT INTO telemetry_reports
    (device_id,boot_id,seq,quality,measured_at,received_at,ts_source,ts_suspect,payload_json)
  VALUES (v_device,v_boot,v_seq,'good',v_ts,v_ts,'device',false,v_payload);
  INSERT INTO telemetry
    (device_id,boot_id,seq,metric,value_number,measured_at,received_at,ts_source,ts_suspect)
  VALUES
    (v_device,v_boot,v_seq,'temperature',__TEMP__,v_ts,v_ts,'device',false),
    (v_device,v_boot,v_seq,'humidity',__HUM__,v_ts,v_ts,'device',false);
  RAISE NOTICE 'STAGING S1: temperature=% C humidity=% RH seq=% UTC=%',
    __TEMP__,__HUM__,v_seq,v_ts;
END $telemetry$;
'@
    Invoke-NWStageSql ($sql.Replace('__BOOT__',$boot).Replace('__TEMP__',$t).Replace('__HUM__',$h).Replace('__SEQ__',$seq))
}
function Set-NWStageDoor([ValidateSet('open','closed')][string]$State) {
    $sql = @'
DO $door$
DECLARE
 v_device text:='NW-IT-DR001';
 v_state text:='__STATE__';
 v_boot text:='a1b2c3d4';
 v_seq bigint;
 v_ts timestamp:=clock_timestamp() AT TIME ZONE 'UTC';
 v_type text;
 v_event_id text;
 v_event jsonb;
 v_snapshot jsonb;
BEGIN
 IF current_database() <> 'nextwireless_staging' THEN RAISE EXCEPTION 'NOT STAGING'; END IF;
 IF NOT EXISTS (SELECT 1 FROM devices WHERE device_id=v_device AND org_code='NW-LOCAL-ORG-001') THEN RAISE EXCEPTION 'S6 staging device missing'; END IF;
 SELECT GREATEST(COALESCE((SELECT MAX(seq) FROM device_events WHERE device_id=v_device),0),
                 COALESCE((SELECT MAX(seq) FROM device_state_snapshots WHERE device_id=v_device),0))+1 INTO v_seq;
 v_type:= CASE WHEN v_state='open' THEN 'DOOR_OPENED' ELSE 'DOOR_CLOSED' END;
 v_event_id:=v_device||'-'||v_boot||'-'||v_seq;
 v_event:=jsonb_build_object('schema_version',1,'device_id',v_device,'boot_id',v_boot,
      'seq',v_seq,'ts',to_char(v_ts,'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'event_id',v_event_id,'event_type',v_type,'data',jsonb_build_object('door_state',v_state));
 INSERT INTO device_events(device_id,event_id,event_type,boot_id,seq,event_at,received_at,payload_json,ts_source,ts_suspect,event_source)
 VALUES(v_device,v_event_id,v_type,v_boot,v_seq,v_ts,v_ts,v_event,'device',false,'device');
 v_seq:=v_seq+1;
 v_snapshot:=jsonb_build_object('schema_version',1,'device_id',v_device,'boot_id',v_boot,'seq',v_seq,
       'ts',to_char(v_ts,'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'state','online','data',jsonb_build_object('door_state',v_state));
 INSERT INTO device_state_snapshots(device_id,boot_id,seq,measured_at,received_at,ts_source,state_json,payload_json,ts_suspect)
 VALUES(v_device,v_boot,v_seq,v_ts,v_ts,'device',jsonb_build_object('door_state',v_state),v_snapshot,false);
 RAISE NOTICE 'STAGING S6: % event %, seq=%',v_state,v_type,v_seq;
END $door$;
'@
    Invoke-NWStageSql ($sql.Replace('__STATE__',$State))
}
function New-NWDemoAlarm([ValidateSet('S1','S6')][string]$Kind,[double]$Temperature=0,[double]$Humidity=0) {
    Assert-NWDemoReady
    $now = Get-NWTimeUtc
    if ($Kind -eq 'S1') {
        $rule = $script:NWDemo.S1Rule
        if (-not $script:NWDemo.TemperatureAlarmKey) {
            $script:NWDemo.TemperatureAlarmStart = $now
            $script:NWDemo.TemperatureAlarmKey = '{0}:{1}:demo-{2}' -f $rule.rule_id,$script:NWS1,(New-NWId)
        }
        $script:NWDemo.TemperatureCount++
        $count = $script:NWDemo.TemperatureCount
        $eventType = 'TEMPERATURE_HIGH'
        $device = $script:NWS1
        $alarmKey = $script:NWDemo.TemperatureAlarmKey
        $start = $script:NWDemo.TemperatureAlarmStart
        $data = @{
            temperature=$Temperature; humidity=$Humidity; unit='C'; humidity_unit='%RH'
            threshold=[double]$script:NWDemo.Threshold; operator='>'
            data_point='temperature'; zone_code='DEFAULT'; site_code=$script:NWSite
            profile_code='NW-IT-S1'; test_source='synthetic-staging'
            summary="SIMULATED: Temperature $Temperature C exceeds threshold $($script:NWDemo.Threshold) C"
        }
    } else {
        $rule = $script:NWDemo.S6Rule
        if (-not $script:NWDemo.DoorAlarmKey) {
            $script:NWDemo.DoorAlarmStart = $now
            $script:NWDemo.DoorAlarmKey = '{0}:{1}:demo-{2}' -f $rule.rule_id,$script:NWS6,(New-NWId)
        }
        $script:NWDemo.DoorCount++
        $count=$script:NWDemo.DoorCount
        $eventType='DOOR_OPEN_TOO_LONG'
        $device=$script:NWS6
        $alarmKey=$script:NWDemo.DoorAlarmKey
        $start=$script:NWDemo.DoorAlarmStart
        $limit = if ($null -ne $rule.PSObject.Properties['open_limit_s'] -and $null -ne $rule.open_limit_s) { [int]$rule.open_limit_s } elseif ($null -ne $rule.PSObject.Properties['duration_s'] -and $null -ne $rule.duration_s) { [int]$rule.duration_s } else { throw 'S6 rule missing open_limit_s/duration_s. Cannot fabricate threshold.' }
        $data = @{
            door_state='open'; profile_code='NW-IT-S6'; zone_code='DEFAULT'; site_code=$script:NWSite
            threshold_seconds=$limit; simulated_open_seconds=($limit+60)
            test_source='synthetic-staging'; duration_evidence='simulated/not measured by Rule Engine'
            summary="SIMULATED: Door remained open longer than configured $limit seconds"
        }
    }
    $sev = switch (([string]$rule.severity).ToUpperInvariant()) { 'LOW' {'Low'} 'MEDIUM' {'Medium'} 'HIGH' {'High'} 'CRITICAL' {'Critical'} default {throw "Unknown severity: $($rule.severity)"} }
    $event = @{event_id=(New-NWId);event_type=$eventType;ts=$now;data=$data}
    $payload = @{
        event_id=(New-NWId);org_code=$script:NWOrg;alarm_key=$alarmKey;episode_key=$alarmKey
        kind='occurrence';rule_id=([string]$rule.rule_id);rule_version=([int]$rule.version)
        device_id=$device;zone_code='DEFAULT';severity=$sev;episode_start=$start
        occurrence_seq=$count;raised_at=$now;event=$event
    }
    Write-Host "POST $Kind occurrence $count/$alarmKey -> Dev1 (TEST DATA)" -ForegroundColor Yellow
    $ack = Send-NWDev1Alarm $payload
    return $ack
}
function Get-NWDemoLatest([string]$Device) {
    Assert-NWDemoReady
    $r = Invoke-RestMethod -Uri "$script:NWDP/api/v1/devices/$Device/latest" -Headers $script:NWDemo.DPHeaders -ErrorAction Stop -TimeoutSec 15
    $r | Select-Object device_id,online_status,data_status,door_state,metrics,latest_event | Format-List
}
function Invoke-NWDemoStep {
    param([Parameter(Mandatory)][ValidateRange(0,10)][int]$Step)
    Assert-NWDemoReady
    Write-Host "`n=== NEXTWIRELESS DEMO / STEP $Step / $(Get-Date -Format 'HH:mm:ss') VN ===" -ForegroundColor Cyan
    switch ($Step) {
        0 {
            # Show a true ONLINE -> OFFLINE visual transition at Step 10.
            Set-NWStageConnection online $script:NWS1
            Set-NWStageConnection online $script:NWS6
            $null = Send-NWDev1Status $script:NWS1 online
            $null = Send-NWDev1Status $script:NWS6 online
            Write-Host 'Both test devices marked ONLINE (synthetic).' -ForegroundColor Green
        }
        1 { $t=[math]::Min(27.50,($script:NWDemo.Threshold-2.5)); Add-NWStageMeasurement $t 58.2; Get-NWDemoLatest $script:NWS1 }
        2 { $t=[math]::Min(28.10,($script:NWDemo.Threshold-1.5)); Add-NWStageMeasurement $t 60.5; Get-NWDemoLatest $script:NWS1 }
        3 { $t=[math]::Round(($script:NWDemo.Threshold+1.5),2); Add-NWStageMeasurement $t 63.0; $null=New-NWDemoAlarm S1 $t 63.0; Get-NWDemoLatest $script:NWS1 }
        4 { $t=[math]::Round(($script:NWDemo.Threshold+3.2),2); Add-NWStageMeasurement $t 65.0; $null=New-NWDemoAlarm S1 $t 65.0; Get-NWDemoLatest $script:NWS1 }
        5 { $t=[math]::Round(($script:NWDemo.Threshold+5.1),2); Add-NWStageMeasurement $t 67.0; $null=New-NWDemoAlarm S1 $t 67.0; Get-NWDemoLatest $script:NWS1 }
        6 { Set-NWStageDoor open; Get-NWDemoLatest $script:NWS6 }
        7 { Set-NWStageDoor closed; Get-NWDemoLatest $script:NWS6 }
        8 { Set-NWStageDoor open; $null=New-NWDemoAlarm S6; Get-NWDemoLatest $script:NWS6 }
        9 { $null=New-NWDemoAlarm S6; Get-NWDemoLatest $script:NWS6 }
        10 {
            Set-NWStageConnection offline $script:NWS1
            $null=Send-NWDev1Status $script:NWS1 offline
            Get-NWDemoLatest $script:NWS1
        }
    }
}
function Get-NWDemoChecklist {
@'
  STEP 0 : Make both devices ONLINE (test-only, before the actual demo)
  STEP 1 : S1 normal   (T below threshold, humidity 58.2%)
  STEP 2 : S1 normal   (T below threshold, humidity 60.5%)
  STEP 3 : S1 high     (Alarm S1 occurrence 1)
  STEP 4 : S1 high     (Alarm S1 occurrence 2)
  STEP 5 : S1 high     (Alarm S1 occurrence 3; optional)
  STEP 6 : S6 DOOR_OPENED -> open
  STEP 7 : S6 DOOR_CLOSED -> closed
  STEP 8 : S6 DOOR_OPENED + simulated door-too-long Alarm 1
  STEP 9 : S6 door-too-long Alarm occurrence 2 (optional)
  STEP 10: NW-IT-TH001 OFFLINE (synthetic, not a physical power cut)

  Run one stage at a time: Invoke-NWDemoStep 0
  Then: Invoke-NWDemoStep 1; ... Invoke-NWDemoStep 10
  Dev1: refresh Dashboard immediately after each stage, before data becomes STALE.
'@
}
function Clear-NWDemoSession {
    if ($null -ne $script:NWDemo) {
        if ($null -ne $script:NWDemo.FrappeHeaders) { $script:NWDemo.FrappeHeaders.Clear() }
        if ($null -ne $script:NWDemo.DPHeaders) { $script:NWDemo.DPHeaders.Clear() }
    }
    $script:NWDemo = $null
    Write-Host 'Demo credentials cleared from script session.' -ForegroundColor Green
}
Write-Host 'Loaded nextWireless FIXED staging demo. Next: Initialize-NWDemo'  -ForegroundColor Cyan
