# Dev 1 Pending Confirmations

The implementation identifies itself as contract `1.3-proposed`; production Bridge/Frappe behavior is not proven by local mock tests.

## NEEDS_DEV1_CONFIRMATION

- Approve the alarm event types, nested event schema, title-case outbound severity and deterministic `{rule}:{device}:{episode-start}` public key.
- Confirm one Alarm per `alarm_key` and how occurrence/clear transitions are represented in Frappe.
- Confirm that `event.data.summary` maps directly to `Alarm.summary`, including localization policy. The code mapping itself is verified.
- Approve ISO weekday values 1..7 and S6 schedule boundaries (08:00 inclusive, 18:00 exclusive, Asia/Ho_Chi_Minh).
- Resolve S6 `door_state` channel conflict: migration seed uses `status`; contract v1.3 example uses `telemetry`.
- Confirm globally unique `device_id` and `profile_code`; current database keys are global and alarm keys omit organization.
- Confirm whether `credential_ref` may be displayed and who can edit it; never place actual credentials in this field.
- Provide production HTTPS Bridge URLs and tokens through a private secret channel; confirm rotation, per-org scope, rate limits and timeouts.
- Confirm snapshot tombstone/deletion semantics. Current code retains omitted resources and has no hard-delete contract.
- Confirm dashboard pagination beyond offset/limit and requirements for time-range, site, zone, alarm status and severity filters.
- Confirm active-alarm KPI semantics where rule condition `cleared` and workflow `closed` are separate.
- Confirm Frappe DocType/field names and whether `in_progress` means assignment, ownership, or only workflow state.

## NOT IMPLEMENTED

- Alarm detail endpoint, per-episode history endpoint and Incident model/API.
- Separate ACK and Close endpoints; only the common lifecycle-state callback exists.
- Telemetry time-range query, aggregation/downsampling and ascending sort.
- Device-list site/zone/status/profile filters.
- Alarm filters and server-side summary/KPI endpoint.
- Production Bridge deployment verification and physical ESP32 S1/S6 verification in this repository state.

## Code/contract differences

| Topic | Implementation | Contract/document | Resolution |
|---|---|---|---|
| Contract status | Routes/models are live in source | v1.3 calls itself proposed | Treat implemented shape as source truth but not production sign-off |
| S6 point channel | seed `status` | example `telemetry` | confirmation required |
| Severity | DB uppercase; Bridge title case | proposal title case | document both and transform |
| Alarm identity | internal occurrence keys plus public wire mapping | public alarm=episode key | use public key at Bridge/Dev 1 boundary |
| Dashboard alarm history | `/alarms` returns occurrence rows | wording may imply Alarm entities | do not claim a dedicated Alarm entity/detail API |
| Snapshot | all-or-nothing apply; omission retained | deletion policy proposed | confirm tombstones before deletes |

## Verification status

Dashboard, sync, rule, outbox, status, Bridge-contract and snapshot tests exist. Most environment-dependent integration suites are skipped unless their `RUN_*` flag and services are provided. A test file's existence is not production E2E evidence.
