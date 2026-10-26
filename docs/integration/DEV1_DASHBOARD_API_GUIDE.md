# Dev 1 Dashboard API Guide

Verified against branch `feat/dev1-integration-v1-3` on 2026-10-24.

## Connection and authentication

Base URL on the current Dev 2 host:

```text
http://192.168.1.87:8002/api/v1
```

Except `/health` and `/outbox/metrics`, Dashboard and integration routes require:

```http
Authorization: Bearer <DP_INBOUND_TOKEN>
X-Org-Code: demo
Accept: application/json
```

The bearer value must be supplied privately. A missing/invalid token returns `401`; an organization outside the token scope returns `403`. Dashboard callers must not use one organization's response to populate another organization's UI.

## Dashboard endpoints

| Method | Path | Purpose | Pagination |
|---|---|---|---|
| GET | `/health` | Data API/PostgreSQL readiness | No |
| GET | `/devices` | Tenant-filtered device inventory and current connectivity | `limit` 1–500, `offset` ≥ 0 |
| GET | `/devices/{device_id}/latest` | Latest metrics and latest device event | No |
| GET | `/devices/{device_id}/telemetry` | Time-series data for charts | `limit` 1–1000, `offset` ≥ 0; optional `metric` |
| GET | `/devices/{device_id}/events` | Device/door event history | `limit` 1–500, `offset` ≥ 0 |
| GET | `/devices/{device_id}/status` | Online/offline state, last seen and status history | `history_limit` 1–100 |
| GET | `/zones/{zone_code}/summary` | Device/online/offline totals for one site/zone | Required `site_code` |
| GET | `/alarms` | Alarm occurrence/clear history and delivery state | `limit` 1–500, `offset` ≥ 0 |
| GET | `/outbox/metrics` | Operational delivery backlog | No |

The actual source also exposes configuration synchronization routes (`PUT /sync/organizations/{org_code}`, `/sync/sites/{org_code}/{site_code}`, `/sync/zones/{org_code}/{site_code}/{zone_code}`, `/sync/profiles/{profile_code}`, `/sync/devices/{device_id}`, `/sync/rules/{rule_id}`) and `POST /alarms/{alarm_key}/state`. They are integration/write APIs, not Dashboard reads.

## Device list

```http
GET /api/v1/devices?limit=50&offset=0
```

```json
{
  "total": 3,
  "limit": 50,
  "offset": 0,
  "devices": [{
    "device_id": "TH-001",
    "org_code": "demo",
    "site_code": "demo-site",
    "zone_code": "office",
    "profile_code": "TEMP_HUM_SENSOR",
    "telemetry_interval_seconds": 60,
    "heartbeat_interval_seconds": 60,
    "enabled": true,
    "current_state": "online",
    "status_updated_at": "2026-10-24T01:20:00Z",
    "last_seen_at": "2026-10-24T01:20:00Z",
    "boot_id": "example-boot"
  }]
}
```

## S1 latest temperature and humidity

```http
GET /api/v1/devices/TH-001/latest
```

```json
{
  "device_id": "TH-001",
  "metrics": [
    {"metric": "humidity", "value": 56.2, "measured_at": "2026-10-24T01:20:00Z"},
    {"metric": "temperature", "value": 28.4, "measured_at": "2026-10-24T01:20:00Z"}
  ],
  "latest_event": null
}
```

`measured_at` is the sensor timestamp used on charts; it is not the API request time.

## Telemetry chart history

```http
GET /api/v1/devices/TH-001/telemetry?metric=temperature&limit=100&offset=0
```

```json
{
  "device_id": "TH-001",
  "total": 240,
  "limit": 100,
  "offset": 0,
  "points": [
    {"metric": "temperature", "value": 28.4, "measured_at": "2026-10-24T01:20:00Z"}
  ]
}
```

Request temperature and humidity separately when drawing two series. Results are newest first.

## S6 door events and latest door state

```http
GET /api/v1/devices/DR-001/events?limit=50&offset=0
```

```json
{
  "device_id": "DR-001",
  "total": 12,
  "limit": 50,
  "offset": 0,
  "events": [{
    "event_id": "DR-001-example-7",
    "event_type": "DOOR_OPENED",
    "boot_id": "example",
    "seq": 7,
    "event_at": "2026-10-24T01:20:00Z",
    "received_at": "2026-10-24T01:20:01Z",
    "payload": {"data": {"door_state": "open"}}
  }]
}
```

For the latest projection use `GET /devices/DR-001/latest`. Its `latest_event` contains `event_type`, `event_at`, and `door_state`. The complete original message remains in the event-history `payload`.

## Device status

```http
GET /api/v1/devices/TH-001/status?history_limit=20
```

```json
{
  "device_id": "TH-001",
  "current_state": "online",
  "last_seen_at": "2026-10-24T01:20:00Z",
  "status_updated_at": "2026-10-24T01:20:00Z",
  "timeout_seconds": 120,
  "history": [
    {"state": "online", "ts": "2026-10-24T01:20:00Z", "boot_id": "example-boot"}
  ]
}
```

`last_seen_at` is refreshed by valid device traffic. `status_updated_at` records the latest status-row update. The history contains transitions, not every telemetry sample.

## Alarm list and history

```http
GET /api/v1/alarms?limit=50&offset=0
```

```json
{
  "total": 2,
  "limit": 50,
  "offset": 0,
  "alarms": [{
    "alarm_key": "RULE-TEMP-HIGH:TH-001:1792638000",
    "episode_key": "ep:internal-example",
    "rule_code": "RULE-TEMP-HIGH",
    "device_id": "TH-001",
    "severity": "HIGH",
    "occurrence_kind": "TRIGGERED",
    "event_type": "THRESHOLD_EXCEEDED",
    "triggered_at": "2026-10-24T01:20:00Z",
    "raised_at": "2026-10-24T01:20:00.000Z",
    "cleared_at": null,
    "status": "open",
    "summary": "Temperature high on TH-001",
    "delivery_state": "DELIVERED",
    "attempt_count": 1,
    "ack_id": "example-ack",
    "delivered_at": "2026-10-24T01:20:01Z"
  }]
}
```

`severity` in this read model is the internal stored value (`INFO`, `WARNING`, `HIGH`, or `CRITICAL`). Outbound Bridge payloads map those values to `Low`, `Medium`, `High`, or `Critical`. `status` is `open`/`cleared` until nextGRP posts a lifecycle value (`acknowledged`, `in_progress`, or `closed`).

## Operational data versus Dev 1 business data

Dev 2 owns operational observations: telemetry, device events, connection state, `last_seen_at`, alarm detections, delivery attempts and ACK metadata. These fields report what the Data Plane received and delivered.

Dev 1 owns business configuration and workflow: Organization/Site/Zone names, Profile and Rule definitions, alarm acknowledgement/assignment/closure, dashboard labels and nextGRP UI state. Dev 2 does not silently repair synchronized Profile/Rule content. Configuration corrections must be sent by Dev 1 with a higher version.

The Dashboard must not interpret `delivery_state=DELIVERED` as proof that a nextGRP screen rendered the alarm; it only proves that the configured Bridge returned a valid ACK.
