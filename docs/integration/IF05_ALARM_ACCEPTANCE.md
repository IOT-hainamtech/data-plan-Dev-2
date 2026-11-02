# IF-05 Alarm acceptance — controlled rollout

Status on 2026-11-02: **BLOCKED**, with no live POST performed.

## Contract

- Endpoint: `POST https://semisweet-conceal-gizzard.ngrok-free.dev/api/method/nextgrp.nextgrp_iot.controller.alarm_controller.receive_alarm`
- Authentication: `Authorization: token API_KEY:API_SECRET`
- Headers: `Content-Type: application/json`, `Accept: application/json`,
  `ngrok-skip-browser-warning: true`, and `X-Frappe-Site-Name: nextgrp.localhost`.
- Body: `{"payload": <frozen mapped alarm>}`.

The ACK is read from `message`. Its `alarm_key` is the nextGRP Alarm document
name and is intentionally not compared with the sent episode key. Delivery is
accepted only when kind, mapped device, severity, changed/duplicate semantics
and occurrence count are valid. Duplicate ACKs additionally require
`replayed=true` and `changed=false`.

## Required mappings and versions

| Internal | nextGRP | Required version |
|---|---|---:|
| `hainam` | `NW-LOCAL-ORG-001` | — |
| `TH-001` | `NW-IT-TH001` | — |
| `DR-001` | `NW-IT-DR001` | — |
| `S1_TEMP_HIGH` | `NW-IT-RULE-S1` | 2 |
| `S6_DOOR_OPEN` | `NW-IT-RULE-S6` | 1 |

Every mapping must exist as enabled and confirmed in
`integration_identity_mappings`. The runtime Rule version, frozen Outbox payload
version and confirmed external mapping version must all match. A `cleared`
occurrence reports condition recovery and is sent regardless of `auto_close`;
only nextGRP decides whether that recovery also closes the Alarm. S6 cleared
remains blocked until Dev 1 confirms its event type; Dev 2 does not guess it.

Runtime version `0` is a bootstrap value: migrations originally seed the Rules,
and migration 009 later adds `config_version BIGINT DEFAULT 0`. It is not proof
of a successful IF-03 synchronization. Do not update it directly.

Before IF-05 acceptance, fetch the authoritative Device configuration from Dev 1
and apply IF-03 through the existing authenticated reconciliation transaction.
Compare S1 threshold/operator/hold/hysteresis/clear hold/cooldown/severity and S6
event/schedule/hold/cooldown/severity/auto-close semantics before accepting the
new versions. Only after atomic apply succeeds may an operator create confirmed
identity mappings backed by the same authoritative IDs and versions. Historical
Outbox payloads remain frozen; version-zero records are not bulk rewritten.

## One-shot acceptance safety

Do not start the global worker. After mappings/configuration and credentials are
verified, the operator selects exact Outbox IDs from one real episode and gives
explicit approval. The one-shot selector requires all of these values:

```text
IF05_APPROVED_OUTBOX_IDS=<occurrence-id[,cleared-id]>
IF05_APPROVED_ORG=hainam
IF05_APPROVED_DEVICE=TH-001|DR-001
IF05_APPROVED_RULE=S1_TEMP_HIGH|S6_DOOR_OPEN
IF05_RUN_ONCE=true
BRIDGE_DELIVER_NOT_BEFORE=<approved UTC activation timestamp with Z>
```

The database claim filters exact IDs, internal tenant, device and Rule. Existing
ordering protection prevents cleared from overtaking an undelivered occurrence.
On retry, the mapped payload and delivery event ID remain frozen.
In production, network/408/429/5xx failures remain durable and retry without an
attempt ceiling, using exponential backoff with bounded delay and jitter. A
one-shot acceptance process handles each exact approved row only once per run.
Other 4xx responses are retained as `FAILED` for reconciliation; they are never
deleted.

Run the worker only through an operator-reviewed command that injects secrets
from the local secret store. Never place the Frappe token in shell history,
Compose source or this document. Verify the ACK and nextGRP read-back before
approving any second record.

## Automatic delivery after acceptance

Automatic delivery requires a new UTC activation cutoff strictly later than all
legacy backlog, completed mappings/config sync, signed-off ACK/read-back, and an
operational decision for legacy reconciliation. Keep the 23 `demo` records and
all pre-activation `hainam` records outside the claim window. Backfill is a
separate, explicitly approved reconciliation activity.
