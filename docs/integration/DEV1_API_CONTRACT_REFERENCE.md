# Dev 1 API Contract Reference

Implementation reviewed 2026-10-26. Base prefix is `/api/v1`. The deployed host is environment-specific and is not part of the contract.

## Authentication and common errors

Only `GET /health` is unauthenticated. Every other main Data API route, including `GET /outbox/metrics`, requires `Authorization: Bearer <service token>` and `X-Org-Code`. Responses include `401` for missing/invalid bearer, `400` for missing organization header, `403` for an organization outside credential scope, and `503` if inbound authentication is not configured. FastAPI validation errors are `422`.

No endpoint implements a sort query. List order is fixed by code. Pagination uses offset/limit and returns `total`, `limit`, `offset`.

## Read/Data API endpoints

| Method and path | Parameters | Response | Empty/missing behavior | Test evidence |
|---|---|---|---|---|
| `GET /health` | none | `{service:string,status:"UP"|"DOWN"}` | DB failure returns 503/DOWN | source only |
| `GET /outbox/metrics` | authenticated organization from headers | `{pending:int,failed:int,delivery_failures:int,oldest_pending_age_seconds:float}` scoped through alarm/device ownership | zero counts; age 0 | dashboard test |
| `GET /devices` | query `limit` 1..500 default 100, `offset>=0` default 0 | `{total,limit,offset,devices[]}` | empty array | dashboard test |
| `GET /devices/{device_id}/latest` | path ID | `{device_id,metrics[],latest_event}` | unknown/disabled device 404; no data gives empty metrics and null event | dashboard test |
| `GET /devices/{device_id}/telemetry` | optional `metric`; limit 1..1000 default 100; offset | `{device_id,total,limit,offset,points[]}` newest first | 404 device; empty points | dashboard test |
| `GET /devices/{device_id}/events` | limit 1..500 default 50; offset | `{device_id,total,limit,offset,events[]}` newest first | 404 device; empty events | dashboard test |
| `GET /devices/{device_id}/status` | `history_limit` 1..100 default 20 | status object plus history | unknown/disabled device 404; absent status projects offline, null times, timeout 120 | dashboard test |
| `GET /zones/{zone_code}/summary` | required query `site_code` | scope/counters | unknown zone 404 | source |
| `GET /alarms` | limit 1..500 default 50; offset | occurrence list | empty array | dashboard test |

### Response field schemas

Device list item: `device_id`, `org_code`, `site_code`, `zone_code`, `profile_code` are strings; `telemetry_interval_seconds`, `heartbeat_interval_seconds` are integers; `enabled` is boolean; `current_state` is `online|offline`; `status_updated_at` and `last_seen_at` are datetime strings or null; `boot_id` is string or null.

Latest metric item: `{metric:string,value:number,measured_at:datetime}`. Latest event is null or `{event_id:string,event_type:string,event_at:datetime,door_state:any|null}`.

Telemetry point: `{metric:string,value:number,measured_at:datetime}`.

Event item: `{event_id:string,event_type:string,boot_id:string,seq:int,event_at:datetime,received_at:datetime,payload:object}`.

Status: `{device_id:string,current_state:enum,last_seen_at:datetime|null,status_updated_at:datetime|null,timeout_seconds:int,history:[{state:enum,ts:datetime,boot_id:string}]}`.

Zone summary: `{org_code:string,site_code:string,zone_code:string,display_name:string,device_count:int,online_count:int,offline_count:int}`.

Alarm item: `alarm_key`, `episode_key`, `rule_code`, `device_id`, `severity`, `occurrence_kind`, `event_type`, `triggered_at`, `status`, `summary`, `delivery_state` are strings; `raised_at`, `cleared_at`, `ack_id`, `delivered_at` are string/datetime or null; `attempt_count` is integer or null. Stored severity is `INFO|WARNING|HIGH|CRITICAL`; occurrence kind is `TRIGGERED|CLEARED`; delivery is `PENDING|IN_FLIGHT|DELIVERED|FAILED|UNKNOWN`. Status is lifecycle state when present, otherwise `cleared` or `open`.

## Configuration and lifecycle write endpoints

All sync bodies use the strict models documented in `DEV1_COMPLETE_FIELD_SPECIFICATION.md`. The URL identifier must equal the body identifier. Success for all versioned writes is:

```json
{"status":"applied","resource_id":"NW-TEST-ID","applied_version":1,"duplicate":false}
```

An identical retry returns status `duplicate`. Older versions return `409` with `STALE_VERSION`; changed content at the same version returns `409` with `VERSION_PAYLOAD_CONFLICT`.

| Method and path | Body model | Extra behavior |
|---|---|---|
| `PUT /sync/organizations/{org_code}` | OrganizationIn | tenant authorization |
| `PUT /sync/sites/{org_code}/{site_code}` | SiteIn | parent org must exist/enabled |
| `PUT /sync/zones/{org_code}/{site_code}/{zone_code}` | ZoneIn | parent site must exist/enabled |
| `PUT /sync/profiles/{profile_code}` | ProfileIn | replaces point/event children transactionally |
| `PUT /sync/devices/{device_id}` | DeviceIn | zone/profile must exist/enabled |
| `PUT /sync/rules/{rule_id}` | RuleIn | validates scope ownership/shape |
| `POST /alarms/{alarm_key}/state` | AlarmStateIn | alarm mapping must exist; states may only advance |

Lifecycle body:

```json
{
  "alarm_key": "NW-TEST-R01:NW-TEST-TH01:1792980000",
  "org_code": "NW-TEST",
  "state": "acknowledged",
  "changed_at": "2026-10-26T03:00:00Z",
  "changed_by": "user@example.invalid",
  "version": 1
}
```

`state` is `acknowledged|in_progress|closed`; `version>=1`. Unknown alarm is 404; state regression is 409. There is no separate ACK or Close endpoint.

## Bridge receive API versus Data API

`POST /internal/events/alarm`, `POST /internal/events/status`, and `GET /internal/snapshot` are implemented by `app/mock_nextgrp.py`, a development Bridge mock—not by the Dashboard Data API. The Data Plane sends events to those routes through configured URLs. They require bearer auth; event POSTs require `Idempotency-Key=event_id` and return HTTP 202 with `{accepted,duplicate,event_id,alarm_key,ack_id}`. Do not expose mock management routes as production API.

## JSON examples (schema-valid synthetic NW-TEST data)

### S1 telemetry (MQTT fixture shape)

```json
{"schema_version":"1.0","device_id":"NW-TEST-TH01","org_code":"NW-TEST","site_code":"NW-TEST-SITE","profile":"TEMP_HUM_SENSOR","boot_id":"nw-test-boot-1","seq":1,"ts":"2026-10-26T03:00:00.000Z","ts_source":"device","type":"telemetry","quality":"good","data":{"temperature":28.4,"humidity":56.2}}
```

### S6 door event (MQTT fixture shape)

```json
{"schema_version":"1.0","device_id":"NW-TEST-DR01","org_code":"NW-TEST","site_code":"NW-TEST-SITE","profile":"DOOR_SENSOR","boot_id":"nw-test-boot-1","seq":2,"ts":"2026-10-26T03:01:00.000Z","ts_source":"device","type":"event","event_id":"NW-TEST-DR01-2","event_type":"DOOR_OPENED","data":{"door_state":"open"}}
```

### Device status (Bridge payload)

```json
{"event_id":"80581a38-1437-5fa2-a763-1dd92c375205","org_code":"NW-TEST","device_id":"NW-TEST-TH01","state":"online","since":"2026-10-26T03:00:00Z","last_seen":"2026-10-26T03:00:00Z","reason":"telemetry","fw_version":"1.0.0"}
```

### Alarm occurrence

```json
{"event_id":"b682ed38-ea5e-5682-a5c4-31daf7810b4c","org_code":"NW-TEST","alarm_key":"NW-TEST-R01:NW-TEST-TH01:1792980000","episode_key":"NW-TEST-R01:NW-TEST-TH01:1792980000","kind":"occurrence","rule_id":"NW-TEST-R01","rule_version":1,"device_id":"NW-TEST-TH01","zone_code":"NW-TEST-ZONE","severity":"High","episode_start":"2026-10-26T03:00:00Z","occurrence_seq":1,"raised_at":"2026-10-26T03:05:00Z","event":{"event_id":"b8acc902-833b-58c2-b27d-c7631f29095f","event_type":"TEMPERATURE_HIGH","ts":"2026-10-26T03:05:00Z","data":{"metric":"temperature","value":31.2,"threshold":30.0,"hold_seconds":300,"hysteresis":1.0,"summary":"Temperature high on NW-TEST-TH01"}}}
```

### Alarm cleared

```json
{"event_id":"61b22ae6-7f94-5e65-b0c3-10222bcf9f7e","org_code":"NW-TEST","alarm_key":"NW-TEST-R01:NW-TEST-TH01:1792980000","episode_key":"NW-TEST-R01:NW-TEST-TH01:1792980000","kind":"cleared","rule_id":"NW-TEST-R01","rule_version":1,"device_id":"NW-TEST-TH01","zone_code":"NW-TEST-ZONE","severity":"High","episode_start":"2026-10-26T03:00:00Z","occurrence_seq":1,"cleared_at":"2026-10-26T03:12:00Z","event":{"event_id":"954e3f41-5c4b-5bf4-89dd-cd6b918f951a","event_type":"TEMPERATURE_RECOVERED","ts":"2026-10-26T03:12:00Z","data":{"metric":"temperature","value":28.8,"recovery_threshold":29.0,"clear_hold_seconds":120,"summary":"Temperature recovered on NW-TEST-TH01"}}}
```

### Dashboard response

```json
{"device_id":"NW-TEST-TH01","metrics":[{"metric":"temperature","value":28.4,"measured_at":"2026-10-26T03:00:00Z"},{"metric":"humidity","value":56.2,"measured_at":"2026-10-26T03:00:00Z"}],"latest_event":null}
```

### Snapshot response

```json
{"contract_version":"1.3-proposed","resources":[{"kind":"organization","key":"NW-TEST","version":1,"payload":{"org_code":"NW-TEST","name":"NW Test Organization","version":1,"enabled":true}}]}
```

The MQTT examples are synthetic fixtures matching ingestion/firmware schema. Bridge alarm/status examples match Pydantic models. Dashboard and snapshot examples match implementation but were not captured from a live production system.

## Requested features not implemented

- Dedicated alarm detail endpoint: **NOT IMPLEMENTED**.
- Dedicated alarm occurrence/history endpoint: **NOT IMPLEMENTED**; use and group `/alarms` rows.
- Dedicated ACK URL and dedicated Close URL: **NOT IMPLEMENTED**; use the lifecycle state endpoint.
- Time-range telemetry filtering, site/zone filtering on device list, server-side alarm filters/sort, cursor pagination: **NOT IMPLEMENTED**.
- Incident API/model: **NOT IMPLEMENTED**.
