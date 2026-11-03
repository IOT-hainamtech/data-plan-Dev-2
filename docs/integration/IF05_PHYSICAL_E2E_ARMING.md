# IF-05 physical E2E acceptance (new Alarm rows only)

This is an operator-controlled acceptance mode, not the production worker mode.
It is disabled unless every guard below is supplied explicitly. It selects only
Alarm Outbox rows whose `id` is greater than the captured high-water mark **and**
whose `created_at` is at or after the captured UTC arm time. Selection is joined
to immutable occurrence/device records and requires enabled, confirmed nextGRP
Organization, Device, and same-version Rule mappings.

It is deliberately restricted to organization `hainam`, devices `TH-001` and
`DR-001`, and rules `S1_TEMP_HIGH` and `S6_DOOR_OPEN`. Status Outbox delivery is
not started. Existing rows, including pending row 956, are outside the window.

## Preconditions

- Build the `outbox-worker` image from the reviewed source.
- Confirm MQTT, Data Plane, Data API and both devices are healthy.
- Confirm the external endpoint and service credential with Dev 1.
- Confirm the global `outbox-worker` service is stopped.
- Never paste the Frappe credential into a command argument or a file.

## A. Arm a bounded physical-test window

Run from the repository root in PowerShell. The database query is read-only.

```powershell
$arm = docker compose exec -T postgres psql -U nw_user -d nextwireless -At -F '|' -c "SELECT COALESCE(MAX(id),0),to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD`"T`"HH24:MI:SS.US`"Z`"') FROM alarm_outbox"
$parts = $arm.Trim().Split('|')
$env:IF05_E2E_HIGH_WATER_ID = $parts[0]
$env:IF05_E2E_ARMED_AT = $parts[1]
$env:IF05_E2E_ARMED = 'true'
$env:IF05_E2E_ORG = 'hainam'
$env:IF05_E2E_DEVICES = 'TH-001,DR-001'
$env:IF05_E2E_RULES = 'S1_TEMP_HIGH,S6_DOOR_OPEN'
$secret = Read-Host 'Frappe API KEY:SECRET' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try { $env:BRIDGE_OUTBOUND_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }

docker compose --profile outbound run --no-deps --rm -d --name nextwireless-if05-e2e `
  -e OUTBOX_DELIVERY_ENABLED=true -e IF05_E2E_ARMED -e IF05_E2E_HIGH_WATER_ID `
  -e IF05_E2E_ARMED_AT -e IF05_E2E_ORG -e IF05_E2E_DEVICES -e IF05_E2E_RULES `
  -e BRIDGE_MODE=real -e BRIDGE_ALARM_PROTOCOL=frappe -e BRIDGE_OUTBOUND_TOKEN `
  -e BRIDGE_ALARM_URL=https://semisweet-conceal-gizzard.ngrok-free.dev/api/method/nextgrp.nextgrp_iot.controller.alarm_controller.receive_alarm `
  -e BRIDGE_STATUS_URL=https://semisweet-conceal-gizzard.ngrok-free.dev/api/method/nextgrp.nextgrp_iot.controller.alarm_controller.receive_alarm `
  -e FRAPPE_SITE_NAME=nextgrp.localhost -e BRIDGE_ALLOW_INSECURE_HTTP=false `
  outbox-worker
```

Do not run this command until the operator explicitly approves ARM. Capturing the
mark does not modify PostgreSQL; starting the worker does modify only newly
eligible Outbox delivery state as part of the approved acceptance flow.

## B. Observe

```powershell
docker logs --tail 100 -f nextwireless-if05-e2e
docker compose exec -T postgres psql -U nw_user -d nextwireless -P pager=off -c "SELECT id,delivery_state,attempt_count,failure_count,ack_id,wire_alarm_key,created_at,delivered_at FROM alarm_outbox WHERE id > $env:IF05_E2E_HIGH_WATER_ID ORDER BY id"
docker compose exec -T postgres psql -U nw_user -d nextwireless -P pager=off -c "SELECT o.id AS outbox_id,a.id AS occurrence_id,a.device_id,a.rule_code,a.occurrence_kind,a.event_type,a.triggered_at,a.cleared_at,a.episode_key,a.payload_json->>'rule_version' AS rule_version FROM alarm_outbox o JOIN alarm_occurrences a ON a.alarm_key=o.alarm_key WHERE o.id > $env:IF05_E2E_HIGH_WATER_ID ORDER BY o.id"
```

The second query deliberately joins by `alarm_key`; Outbox and occurrence identity
sequences are independent.

## C. Stop and return to worker-off state

```powershell
docker stop nextwireless-if05-e2e
Remove-Item Env:BRIDGE_OUTBOUND_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:IF05_E2E_ARMED,Env:IF05_E2E_HIGH_WATER_ID,Env:IF05_E2E_ARMED_AT,Env:IF05_E2E_ORG,Env:IF05_E2E_DEVICES,Env:IF05_E2E_RULES -ErrorAction SilentlyContinue
docker compose ps -a outbox-worker
```

Stopping this acceptance container does not change pending rows. Transient
failures remain durable and can be retried only by deliberately arming the same
window again. Permanent 4xx failures remain retained as `FAILED` for review.

## Pending row 956

Row 956 is intentionally excluded from a newly armed window. Closing that prior
S1 episode requires a separate fixed-ID one-shot approval using
`IF05_APPROVED_OUTBOX_IDS=956`; never combine it with E2E ARM mode.
