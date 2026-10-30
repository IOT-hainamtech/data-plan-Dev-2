# Dev 1 ↔ Dev 2 Phase-1 E2E

The fixtures use only the reserved integration identities `NW-IT-TH001` and
`NW-IT-DR001`. They must exist and be approved in Dev 1 before a LAN run. The
runner never reads `.env` and never prints the service token.

## Confirmed endpoint mapping

| Flow | Method and path | Request | Response/auth | State |
|---|---|---|---|---|
| Config | `GET /api/method/nextgrp.nextgrp_iot.controller.config_controller.get_device_config?device_id=...` | query string | JSON `message`; `Authorization: token API_KEY:API_SECRET`; site header | READY for read-only |
| Status | `POST ...status_controller.receive_status` | form `device_id`, `state` | JSON `message`; exact ACK schema unconfirmed | PARTIAL/fail-closed; no send |
| Alarm | `POST ...alarm_controller.receive_alarm` | form `payload=JSON_STRING` | JSON `message`; Outbox retains stable `event_id` | READY for approved staging |
| Command claim | `POST ...command_controller.claim_command` | form `device_id` | JSON `message` | BLOCKED for demo S1/S6 |
| Command ACK | `POST ...command_controller.ack_command` | form `command_id`, `delivery_token`, `status`, `result_json` | JSON `message` | BLOCKED for demo S1/S6 |
| IF-06 callback | Dev 2 `POST /api/v1/alarms/{alarm_key}/state` | JSON plus Bearer and `X-Org-Code` | Dev 2 applied/duplicate response | Dev 2 READY; Dev 1 BLOCKED |
| Dev 2 reads | `GET /api/v1/devices/{id}/latest`, `/telemetry`, `/events`, `/status`; `/api/v1/health` | Bearer and organization header except health | JSON | READY |

HTTP LAN is rejected unless `DEV1_ALLOW_HTTP_LAN=true`. TLS verification is
never disabled for HTTPS.

## Authentication Contract

Dev 1 Frappe contract requires:
```http
Authorization: token API_KEY:API_SECRET
X-Frappe-Site-Name: nextgrp.localhost
Accept: application/json
```

Supply credentials via process environment only (either combined or pair):
- Combined: `$env:DEV1_SERVICE_TOKEN='KEY:SECRET'`
- Or pair: `$env:DEV1_API_KEY='KEY'`; `$env:DEV1_API_SECRET='SECRET'`

Credentials missing either component fail closed immediately.

## Commands

Offline validation (safe default):

```powershell
docker compose run --rm --no-deps -e PYTHONPATH=/repo/app:/repo `
  -v "${PWD}:/repo" -w /repo data-plane `
  python -m tests.e2e.dev1_dev2.runner --mode offline
```
Or directly on host:
```powershell
$env:PYTHONPATH="app;."
python -m tests.e2e.dev1_dev2.runner --mode offline
```

Read-only LAN/config check; supply a new token only in the process environment:

```powershell
$env:DEV1_BASE_URL='http://192.168.1.43:8000'
$env:DEV1_SITE_NAME='nextgrp.localhost'
$env:DEV1_ALLOW_HTTP_LAN='true'
$env:DEV1_SERVICE_TOKEN='<API_KEY>:<API_SECRET>'
python -m tests.e2e.dev1_dev2.runner --mode read-only
```

Staging POST is double-gated and must only be run after Dev 1 approves both IDs:

```powershell
$env:DEV1_E2E_ALLOW_POST='NW-IT-ONLY'
$env:DEV1_E2E_APPROVED_DEVICES='NW-IT-TH001,NW-IT-DR001'
python -m tests.e2e.dev1_dev2.runner --mode post
```

Unset `DEV1_SERVICE_TOKEN`, `DEV1_API_KEY`, `DEV1_API_SECRET`, and the two POST opt-in variables after the session.
