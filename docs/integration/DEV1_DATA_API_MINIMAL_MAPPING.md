# Minimal Dev 1 read integration without registry cutover

Dev 2 keeps its runtime identities unchanged. The Dev 1 adapter owns this fixed,
reviewed translation:

| Dev 1 identity | Dev 2 Data API identity |
|---|---|
| `NW-LOCAL-ORG-001` | `hainam` |
| `NW-LOCAL-SITE-001` | `hq` |
| `NW-IT-TH001` | `TH-001` |
| `NW-IT-DR001` | `DR-001` |

The adapter must not accept an arbitrary organization/device mapping from a UI
or request parameter. It calls the existing Dev 2 read endpoints with the mapped
internal identity and maps the validated response back to the Dev 1 identity.

## Authentication boundary

Dev 1 calling Dev 2 Data API:

```http
Authorization: Bearer <DP_INBOUND_TOKEN>
X-Org-Code: hainam
```

Dev 2 calling Frappe is a different trust direction and uses:

```http
Authorization: token API_KEY:API_SECRET
X-Frappe-Site-Name: nextgrp.localhost
```

Using the Frappe header against Data API correctly returns HTTP 401. A missing
organization returns 400, and an organization outside the Bearer credential's
allowlist returns 403. Never weaken these checks to make mapping work.

## Read endpoints

- `/api/v1/devices/TH-001/latest`
- `/api/v1/devices/TH-001/telemetry`
- `/api/v1/devices/TH-001/status`
- `/api/v1/devices/DR-001/latest`
- `/api/v1/devices/DR-001/events`
- `/api/v1/devices/DR-001/status`

The current runtime credential is already scoped for `hainam`; no Data Plane
registry migration is required. Dev 1 must keep its dedicated Data API Bearer
token in its secret store and must not reuse its Frappe API key/secret.

## Repeatable read-only verification

Set the credential only in the process environment. For LAN HTTP, explicitly
opt in because TLS termination is not being used on port 8002:

```powershell
$env:DATA_API_URL='http://192.168.1.40:8002'
$env:DATA_API_ALLOW_HTTP_LAN='true'
$env:DP_INBOUND_TOKEN='<dedicated Data API service token>'
python -m tests.e2e.dev1_dev2.data_api_read_client
Remove-Item Env:DP_INBOUND_TOKEN
```

The client performs GET only, never prints the token, validates the returned
internal identity, and emits the translated Dev 1 identity. `LIVE` acceptance
requires a new sequence/timestamp from the physical ESP32, not merely HTTP 200.

Alarm/status POST and Outbox delivery remain out of scope. Keep Outbox Worker
off until Dev 1 has signed off the write contract and backlog policy.
