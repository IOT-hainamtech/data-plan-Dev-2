# Dev 1 Complete Field Specification

Generated from the Dev 2 implementation on 2026-10-26. The source code and migrations are authoritative. `PROPOSED` means a UI recommendation, not an implemented field. Every atomic field is also listed in `DEV1_FIELD_MATRIX.csv`.

## Conventions and ownership

- All PostgreSQL `TIMESTAMP WITHOUT TIME ZONE` values represent UTC; API timestamps are ISO 8601 strings ending in `Z`.
- Dev 1 owns Organization, Site, Zone, Profile, Device and Rule configuration and sends versioned resources to Dev 2.
- Dev 2 owns telemetry, device events/status, rule runtime, alarm detections, outbox delivery and ACK metadata.
- `org_code` is supplied to protected API calls through `X-Org-Code`; bearer authentication is required. Device IDs and profile codes are currently globally keyed.
- JSON models reject unknown fields (`extra="forbid"`). Versioned sync is monotonic: an identical same-version payload is a duplicate; changed same-version or an older version returns `409`.

## Management configuration

| Entity | Implemented fields | Validation / relationships | Ownership | Evidence |
|---|---|---|---|---|
| Organization API | `org_code`, `name`, `version`, `enabled=true` | `version >= 1`; path/body `org_code` must match | Dev 1 editable | `app/api/models.py`, `app/api/integration.py` |
| `organizations` | `org_code`, `display_name`, `enabled`, `metadata_json`, `config_version`, `payload_hash` | PK `org_code`; metadata defaults `{}` | Dev 2 projection | `db/migrations/008_multi_site_registry.sql`, `009_dev1_integration_v1_3.sql` |
| Site API | `org_code`, `site_code`, `name`, `version`, `timezone="UTC"` | `version >= 1`; organization must exist/enabled | Dev 1 editable | `app/api/models.py`, `registry/sync.py` |
| `sites` | `org_code`, `site_code`, `display_name`, `timezone`, `enabled`, `metadata_json`, `config_version`, `payload_hash` | PK `(org_code,site_code)`; FK organization | Dev 2 projection | migration 008/009 |
| Zone API | `org_code`, `site_code`, `zone_code`, `name`, `version` | parent site must exist/enabled | Dev 1 editable | models/integration/sync |
| `zones` | `org_code`, `site_code`, `zone_code`, `display_name`, `enabled`, `metadata_json`, `config_version`, `payload_hash` | PK triple; composite FK site | Dev 2 projection | migration 008/009 |
| Profile API | `profile_code`, `version`, `signal_model`, `protocol`, `expected_interval_s`, `heartbeat_interval_s`, `offline_after_missed`, `data_points[]`, `event_types[]` | intervals `>0`; `signal_model=TELEMETRY|EVENT`; `protocol=MQTT` | Dev 1 editable | `app/api/models.py` |
| Data point API | `code`, `label`, `data_type`, `channel`, `unit`, `min`, `max`, `allowed_values`, `precision`, `required` | type `number|string|boolean`; channel `telemetry|status`; precision 0..12 | Dev 1 editable | `app/api/models.py` |
| Profile tables | `device_profiles`: code/name/enabled/metadata/version/hash; `device_profile_points`: profile/channel/code/type/unit/min/max/allowed/required/precision/metadata; `device_profile_events`: profile/event/state point/value | point PK `(profile,channel,point)`; event PK `(profile,event_type)` | Dev 2 projection | migration 008/009 |
| Device API | `device_id`, `org_code`, `site_code`, `zone_code`, `profile_code`, `status`, `connectivity_type`, `config_version`, `enabled`, `credential_ref` | status `registered|active|maintenance|retired`; connectivity `WIFI|LAN|4G|5G`; version >=1 | Dev 1 editable | `app/api/models.py` |
| `devices` | `device_id`, scope codes, `profile_code`, `enabled`, intervals, `metadata_json`, `credential_ref`, `connectivity`, version/hash | PK device; FKs site/zone/profile; intervals >0 | Dev 2 projection | migration 001/008/009 |
| Rule API | `rule_id`, `version`, `org_code`, `rule_type`, `scope`, threshold/event fields, timing fields, schedule, severity, `auto_close`, `active` | THRESHOLD requires `data_point`,`threshold`; STATE_CHANGE requires `event_type`,`open_limit_s`; ISO weekdays 1..7 | Dev 1 editable | `app/api/models.py` |
| `rule_definitions` | rule identity/scope, kind, metric/event, thresholds/timers, schedule, internal severity, enabled, version/hash/config JSON/timestamps | kind `THRESHOLD|STATE_CHANGE`; scope shape check | Dev 2 projection | migrations 002/003/008/009 |

### Profile data semantics

| Profile | Point/event | Type | Unit / enum | Actual source |
|---|---|---|---|---|
| S1 `TEMP_HUM_SENSOR` | `temperature` | number | `Cel` (display as °C) | MQTT `data.temperature`, `telemetry.value_number` |
| S1 `TEMP_HUM_SENSOR` | `humidity` | number | `%RH` | MQTT `data.humidity`, `telemetry.value_number` |
| S6 `DOOR_SENSOR` | `door_state` | string | `open|closed` | MQTT event payload `data.door_state` |
| S6 `DOOR_SENSOR` | `DOOR_OPENED`, `DOOR_CLOSED` | event type | enum | `device_events.event_type` |

Note: migration seed 008 declares S6 `door_state` on channel `status`; contract v1.3 example declares it on `telemetry`. The ingest path uses the event envelope and profile event mapping, so Dev 1 must confirm the desired profile channel.

## MQTT and device data

The accepted envelope is implemented in `app/ingestion/contracts.py` and emitted by firmware/simulator. Common fields are `schema_version`, `device_id`, `org_code`, `site_code`, `profile`, `boot_id`, `seq`, `ts`, `ts_source`, `type`, and `data`. Telemetry additionally uses `quality` (`good|error`) and optional `errors`; events use `event_id`, `event_type`, and event data. Unknown/invalid profile points are rejected and registry scope must match the registered device.

| Store | Purpose | Fields | Keys / validation |
|---|---|---|---|
| `telemetry_reports` | Preserve each accepted report, including error quality | `id`, `device_id`, `boot_id`, `seq`, `quality`, `measured_at`, `received_at`, `payload_json` | unique `(device_id,boot_id,seq)`; quality `good|error` |
| `telemetry` | Numeric chart points | `id`, `device_id`, `boot_id`, `seq`, `metric`, `value_number`, `measured_at`, `received_at`, `ts_source` | unique `(device_id,metric,measured_at)`; `ts_source=device|server` |
| `device_events` | S6 and other events | `id`, `device_id`, `event_id`, `event_type`, `boot_id`, `seq`, `event_at`, `received_at`, `payload_json` | unique `(device_id,event_id)` |
| `device_status` | Current connectivity projection | `device_id`, `current_state`, `received_at`, `boot_id`, `last_seen_at`, `timeout_seconds` | one row/device; state `online|offline` |
| `device_status_history` | Connectivity transitions | `id`, `device_id`, `state`, `received_at`, `boot_id` | state `online|offline` |

`last_seen_at` is system generated from valid traffic. `received_at`/`status_updated_at` is the projection update time. They must not be merged in the UI.

## Rule evaluation and alarm lifecycle

Internal severity is `INFO|WARNING|HIGH|CRITICAL`; Bridge severity maps respectively to `Low|Medium|High|Critical`. Alarm occurrence kinds in PostgreSQL are `TRIGGERED|CLEARED`; Bridge `kind` is `occurrence|cleared`.

Threshold rules use `threshold_value`, `hysteresis_value`, `hold_seconds`, `clear_hold_seconds`, and `quiet_seconds`. Samples at or below `threshold-hysteresis` begin recovery. A gap over 120 seconds resets continuity. Door rules react to `DOOR_OPENED`/`DOOR_CLOSED`; the seeded schedule is 08:00 inclusive to 18:00 exclusive in `Asia/Ho_Chi_Minh`, with outside-hours firing immediately and in-hours firing after the hold.

State transition implemented by code:

```text
normal --condition held--> active episode / TRIGGERED
active --recovery held--> condition cleared / CLEARED
open --Dev1 callback--> acknowledged --> in_progress --> closed
```

`cleared` is a sensor/rule condition transition. `closed` is a Dev 1 user/workflow transition. They are independent and must never be treated as synonyms.

| Entity | Fields / identity | Notes | Evidence |
|---|---|---|---|
| `rule_runtime_state` | rule/device, hold/recovery timestamps, active flag, sample/fire timestamps, episode identity/start, current state/change, source event | System generated | migration 002/003 |
| `alarm_occurrences` | `alarm_key`, `episode_key`, kind, rule/device/severity/event, trigger/clear timestamps, payload | Unique internal alarm key and unique episode/kind | migration 002/003 |
| Bridge alarm | delivery `event_id`, scope, public `alarm_key=episode_key`, `kind`, rule/version, device/zone, title-case severity, episode time, sequence, one transition timestamp, nested event | Pydantic enforces equal keys and required transition timestamp | `app/api/models.py`, `app/rules/engine.py` |
| `alarm_lifecycle` | public key, org, `state`, rank, version/hash, actor/note/update time | states `open|acknowledged|in_progress|closed`; callback accepts only last three and forbids regression | migration 009, integration router |

## Outbox, Bridge and snapshot

| Contract | Important fields and rules | Evidence |
|---|---|---|
| Alarm outbox | row ID, internal/public alarm keys, JSON payload, delivery UUID, state, attempts/failures, retry/lock/error/HTTP/ACK/delivery/create timestamps | migrations 002/004/009 |
| Status outbox | delivery UUID, device, payload, same delivery fields | migration 009 |
| Delivery | `Idempotency-Key` equals top-level `event_id`; valid ACK requires HTTP 202, `accepted=true`, matching IDs and non-empty `ack_id` | `app/integrations/nextgrp.py`, `app/outbox/worker.py` |
| Snapshot | `{contract_version:"1.3-proposed", resources:[{kind,key,version,payload}]}`; kinds applied organization→site→zone→profile→device→rule | `app/integrations/snapshot.py` |
| Snapshot deletion | Omitted resources are retained; transaction is all-or-nothing | snapshot implementation/tests |

## Implementation versus proposal

- Implemented database/API/event fields are in the CSV with `VERIFIED`.
- Display-only labels, stale indicators, KPI aggregation and DocType conveniences are marked `PROPOSED` and must not be persisted as telemetry.
- There is no Incident table/model/API.
- There is no alarm-detail endpoint; `/api/v1/alarms` is an occurrence list/projection.
- There is no separate ACK and Close URL. Both use `POST /api/v1/alarms/{alarm_key}/state` with lifecycle state and a monotonic version.

## Source index

Primary evidence: `db/migrations/001_schema.sql` through `010_bridge_mock_durable_inbox.sql`; `app/api/models.py`; `app/api/application.py`; `app/api/integration.py`; `app/ingestion/contracts.py`; `app/ingestor.py`; `app/status/service.py`; `app/rules/engine.py`; `app/outbox/worker.py`; `app/integrations/snapshot.py`; firmware message-contract and payload-builder sources; integration/unit tests.
