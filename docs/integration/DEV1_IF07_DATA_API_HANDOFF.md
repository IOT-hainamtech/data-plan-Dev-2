# IF-07 Data API handoff for nextGRP Dev 1

## Connection

- HTTPS base URL while the managed tunnel is active:
  `https://sequence-precision-bubbly.ngrok-free.dev`
- Local diagnostic URL: `http://127.0.0.1:8002`
- Headers: `Authorization: Bearer <service token>`, `X-Org-Code: hainam`,
  `Accept: application/json`.

The token is a Data Plane inbound service token and is unrelated to Frappe's
`Authorization: token KEY:SECRET`. Keep it only in the Dev 1 backend secret
store. Never expose it to Dashboard JavaScript.

## Device mapping and endpoints

| nextGRP context | Data Plane request |
|---|---|
| `NW-IT-TH001` | `GET /api/v1/devices/TH-001/latest` |
| S1 history | `GET /api/v1/devices/TH-001/telemetry?metric=temperature&limit=100` |
| `NW-IT-DR001` | `GET /api/v1/devices/DR-001/latest` |
| S6 transitions (extension) | `GET /api/v1/devices/DR-001/events?limit=50&offset=0` |
| Office summary | `GET /api/v1/zones/office/summary?site_code=hq` |
| Entrance summary | `GET /api/v1/zones/entrance/summary?site_code=hq` |
| Service health | `GET /api/v1/health` |

The events endpoint is a read-only Dashboard extension and does not replace
IF-05 Alarm delivery.

History supports `from`, `to` (ISO-8601 with UTC offset), `metric`, `limit`,
opaque `cursor`, and optional `agg=avg|min|max` plus `step`. Use the returned
`next_cursor`; do not construct cursors. Aggregation and cursor are mutually
exclusive.

## Freshness and display

For S1, compare `boot_id`, `seq`, `measured_at_utc`, `received_at_utc`,
`quality`, and `data_status`. Metrics contain temperature/humidity values and
units. For S6, use `latest_snapshot.door_state`, its `seq`, timestamps and
`data_status`; `latest_event` describes the most recent physical transition.
`online_status` is independent of whether a transition occurred recently.

Map the response back to the authorized nextGRP IDs only after verifying
`organization == hainam` and the requested internal `device_id`. A closed → open
→ closed physical test must show new event IDs/sequences; polling must never
manufacture a transition.

Dashboard polling is owned by Dev 1 and should be configuration-driven. Keep
API response time, ESP32 sampling/heartbeat interval and Dashboard refresh
interval as three separate measurements. A 60-second Dashboard refresh is not
equivalent to Data API latency.

## Error handling

- `401`: missing/invalid Bearer credential; stop and rotate/check the backend secret.
- `403`: organization outside token scope; do not retry under another tenant.
- `404`: mapped device/zone does not exist in the authorized tenant.
- `422`: invalid time range, cursor, aggregation or query parameter.
- `503`: API/database health failure; retry with bounded backoff.
- Timeout/network error: bounded retry; retain the last UI value as stale, not live.

The unauthenticated health endpoint is suitable only for liveness. Device data
endpoints always require tenant authentication.
