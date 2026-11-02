# Dev 2 ↔ Dev 1 integration readiness

Audit date: 2026-11-02.

## Architecture decision

Raw telemetry and raw device events are not pushed continuously to nextGRP.
Data Plane remains the system of record for telemetry/event storage and exposes
them to the nextGRP backend through IF-07 Data API. The Dev 1 ingestion methods
`receive_telemetry` and `receive_event`, although available, are outside the
mandatory BRD/URD flow currently being implemented.

The supported integration boundaries are:

- IF-01/02/03: nextGRP → Data Plane configuration synchronization.
- IF-04: Data Plane → nextGRP online/offline status.
- IF-05: Data Plane → nextGRP Alarm occurrence/cleared.
- IF-06: nextGRP → Data Plane Alarm lifecycle callback.
- IF-07: nextGRP backend → Data API read-only Dashboard queries.

The Alarm/Status adapters and configuration synchronization remain independent.
The Alarm Outbox Worker remains profile-gated and disabled by default; existing
PENDING rows must not be sent as part of IF-07 verification.

## Identity mapping

| Data Plane | nextGRP |
|---|---|
| `hainam` | `NW-LOCAL-ORG-001` |
| `hq` | `NW-LOCAL-SITE-001` |
| `TH-001` | `NW-IT-TH001` |
| `DR-001` | `NW-IT-DR001` |
| `S1_TEMP_HIGH` | `NW-IT-RULE-S1` |
| `S6_DOOR_OPEN` | `NW-IT-RULE-S6` |

Mapping belongs in the trusted Dev 1 backend/integration layer. The frontend
must not choose arbitrary internal organization or device IDs. PostgreSQL
Registry, ESP32 NVS and MQTT identities remain `hainam/hq/TH-001/DR-001`.

## IF-07 authentication

Data API authentication is not Frappe token authentication:

```http
Authorization: Bearer <DP_INBOUND_TOKEN>
X-Org-Code: hainam
Accept: application/json
```

The credential must be held by the Dev 1 backend, not the browser. The API
rejects a missing/invalid Bearer token and rejects organizations outside the
credential's configured scope.

See [DEV1_IF07_DATA_API_HANDOFF.md](DEV1_IF07_DATA_API_HANDOFF.md) for endpoint,
mapping, freshness and error-handling details.
