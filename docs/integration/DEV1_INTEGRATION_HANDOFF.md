# Dev 1 Integration Handoff

Status date: 2026-10-23  
Contract: nextWireless M1 v1.3 proposed  
Dev 2 Data API: `http://192.168.1.87:8002`

## DEV2 DONE

- Data API supports idempotent, versioned sync for Organization, Site, Zone, Profile, Device and Rule.
- Tenant scope is enforced by bearer authentication and `X-Org-Code`.
- Alarm state callback supports monotonic `acknowledged -> in_progress -> closed` changes, duplicate detection and stale/regressive update rejection.
- Durable alarm and status outboxes deliver with an `Idempotency-Key`, require HTTP `202`, validate the ACK and retry with bounded exponential backoff.
- Alarm occurrence and cleared events use the same public `alarm_key`/`episode_key` for an episode. `event.data.summary` is populated for direct mapping to nextGRP `Alarm.summary`.
- Snapshot reconciliation is ordered and all-or-nothing. Resources omitted by a snapshot are retained; M1 does not interpret omission as deletion.
- Dashboard read APIs are tenant filtered and use bounded `limit` parameters.
- Mock Bridge validates the v1.3 alarm/status bodies, authentication, event identity, episode identity and duplicate/conflict semantics.

## Payload mapping

| Dev 2 field | nextGRP target | Notes |
|---|---|---|
| `event_id` | delivery/idempotency identity | Must equal `Idempotency-Key` |
| `alarm_key` | Alarm external key | Stable across occurrence and clear |
| `episode_key` | Alarm episode key | Must equal `alarm_key` in M1 |
| `event.data.summary` | `Alarm.summary` | Human-readable, stable English summary |
| `rule_id`, `rule_version` | rule provenance | Version is at least 1 |
| `device_id`, `zone_code`, `org_code` | ownership links | Used for tenant routing |
| `kind` | transition | `occurrence` or `cleared` |
| `raised_at`, `cleared_at` | transition timestamp | Exactly one is required by transition kind |

Status events contain `event_id`, `org_code`, `device_id`, `state`, `since`, optional `last_seen`, and `reason`. The Bridge must return HTTP `202` and a JSON ACK containing `accepted=true`, the same `event_id`, and a non-empty `ack_id` (alarm ACKs also return the same `alarm_key`).

## DEV1 ACTION REQUIRED

The synchronized `NW-TEST-*` configuration currently stored at Dev 2 is internally inconsistent and must be corrected by a new, higher config version; Dev 2 will not mutate Dev 1-owned configuration directly.

| Resource | Observed state | Required correction |
|---|---|---|
| `NW-TEST-TH` | Contains boolean `door_state` telemetry | Send temperature/humidity numeric points for the S1 profile |
| `NW-TEST-DOOR` | Contains temperature/humidity points | Send door event definitions, including `DOOR_OPENED` and `DOOR_CLOSED` |
| `NW-TEST-R01` | Threshold rule is disabled | Enable it when S1 validation is intended |
| `NW-TEST-R02` | Event type is `chống cháy` | Send the canonical `DOOR_OPENED` event type and confirmed schedule/hold values |

Dev 1 should increment each affected version and resend Profile before Device and Rule. Reusing version 1 with different content is correctly rejected as a conflict.

For Real Bridge activation, Dev 1 must provide through the agreed secret channel:

- outbound bearer token;
- confirmed alarm/status/snapshot schemas and ACK examples;
- confirmation that HTTP on the trusted LAN is acceptable, or preferably an HTTPS endpoint;
- snapshot ownership/deletion semantics.

## BLOCKED

- Automatic delivery to `http://192.168.1.83:8081` is not enabled because `BRIDGE_OUTBOUND_TOKEN` and final schema confirmation are not available in the Dev 2 runtime environment.
- Snapshot remains connected to Mock Bridge. It must not be pointed at Real Bridge until authentication, tenant coverage and omission/deletion semantics are confirmed.

## VERIFICATION PENDING

- One controlled Real Bridge occurrence, duplicate, clear, offline and recovery cycle after credentials are installed.
- Confirmation in the nextGRP UI that one episode is updated rather than duplicated.
- Physical ESP32 S1/S6 validation; the current evidence uses the TLS simulator.

## Safe activation checklist

1. Put the token only in the ignored `.env`/secret store; never commit it.
2. Set `BRIDGE_ALARM_URL` and `BRIDGE_STATUS_URL`; keep snapshot on Mock initially.
3. Set `BRIDGE_ALLOW_INSECURE_HTTP=true` only for the explicitly approved trusted-LAN HTTP endpoint.
4. Recreate only `outbox-worker`, then send one synthetic episode and verify HTTP 202/ACK/duplicate behavior.
5. Configure `BRIDGE_SNAPSHOT_URL` and recreate `snapshot-worker` only after snapshot validation succeeds.

