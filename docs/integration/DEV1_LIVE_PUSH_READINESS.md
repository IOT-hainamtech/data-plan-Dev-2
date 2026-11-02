# Dev 2 → Dev 1 live push readiness

Audit date: 2026-11-02. Confirmed HTTPS base URL:
`https://semisweet-conceal-gizzard.ngrok-free.dev`.

## Confirmed methods

| Data | Frappe method | Contract status |
|---|---|---|
| Device configuration | `config_controller.get_device_config` | Confirmed GET |
| Connection status | `status_controller.receive_status` | POST transport confirmed; ACK contract remains partial |
| Alarm occurrence/cleared | `alarm_controller.receive_alarm` | Confirmed POST, `payload=JSON_STRING` |
| Command claim/ACK | `command_controller.claim_command`, `ack_command` | Known, not used by S1/S6 live push |
| Raw telemetry | — | **BLOCKED: no endpoint/schema/ACK in confirmed contract** |
| Raw device event | — | **BLOCKED: no endpoint/schema/ACK in confirmed contract** |

Do not derive names such as `receive_telemetry` or `receive_event`. Do not send
temperature, humidity, or door transitions through the Alarm endpoint.

## Authentication

Outbound Frappe requests require a dedicated `API_KEY:API_SECRET` pair:

```http
Authorization: token API_KEY:API_SECRET
X-Frappe-Site-Name: nextgrp.localhost
Accept: application/json
```

This is unrelated to Data API's inbound Bearer token. The credential must be
provided at runtime through `DEV1_SERVICE_TOKEN`, or the separate
`DEV1_API_KEY` and `DEV1_API_SECRET` variables. Current runtime inspection found
no Frappe service credential, so authenticated GET and POST remain blocked.

## Approved identity mapping

| Dev 2 | Dev 1 |
|---|---|
| `hainam` | `NW-LOCAL-ORG-001` |
| `hq` | `NW-LOCAL-SITE-001` |
| `TH-001` | `NW-IT-TH001` |
| `DR-001` | `NW-IT-DR001` |
| `S1_TEMP_HIGH` | `NW-IT-RULE-S1` |
| `S6_DOOR_OPEN` | `NW-IT-RULE-S6` |

The mapping must be frozen per outbound record and never inferred from a device
name. Runtime Data Plane registry values remain unchanged.

## Required contract before implementation

Dev 1 must provide, separately for telemetry and device events:

1. Exact whitelisted Frappe method path and whether it is deployed on the HTTPS base URL.
2. HTTP method, content type and exact form fields (or JSON body).
3. Required/optional fields, types, units, timestamp semantics and schema version.
4. Identity fields after mapping, including organization/site/profile/device.
5. Stable idempotency key and duplicate behavior.
6. Exact `response.message` ACK schema proving the record was persisted.
7. Error semantics for 400/401/403/404/409/422/429/5xx.
8. A read-back API or DocType query that confirms the saved record/device.

Only then may Dev 2 add durable cursors/outboxes. Automatic delivery must use
bounded exponential retry for 408/429/5xx/network failures, preserve identical
payload and idempotency key on retry, stop on contract errors, redact credentials,
and never claim the existing Alarm Outbox backlog.

## Safe verification commands

Connectivity without credentials:

```powershell
Invoke-RestMethod 'https://semisweet-conceal-gizzard.ngrok-free.dev/api/method/ping'
```

Authenticated configuration GET after a new service credential is installed in
the process environment:

```powershell
$env:DEV1_BASE_URL='https://semisweet-conceal-gizzard.ngrok-free.dev'
$env:DEV1_SITE_NAME='nextgrp.localhost'
$env:DEV1_SERVICE_TOKEN='<API_KEY>:<API_SECRET>'
$env:PYTHONPATH='app;.'
python -m tests.e2e.dev1_dev2.runner --mode read-only
Remove-Item Env:DEV1_SERVICE_TOKEN
```

Do not use `--mode post` for live telemetry/event. Its existing POST mode is an
Alarm staging test with synthetic fixtures and is not authorized for runtime.
