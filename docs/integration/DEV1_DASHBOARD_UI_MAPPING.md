# Dev 1 Dashboard UI Mapping

All screens consume the protected Data API with bearer authentication and `X-Org-Code`, except health and outbox metrics. Telemetry and event history remain in Dev 2; Dev 1 must not duplicate them into DocTypes.

## Dashboard Overview

| UI Component | Display Label | Source Field | API Endpoint | Data Type | Read/Write | Notes |
|---|---|---|---|---|---|---|
| KPI | Devices | `total` | `GET /api/v1/devices` | integer | Read | Implemented |
| KPI | Online | `devices[].current_state` count | `GET /api/v1/devices` | integer | Read | Client aggregation; PROPOSED |
| KPI | Offline | same | same | integer | Read | Null DB state projects as offline |
| KPI | Active Alarms | `alarms[].status` count | `GET /api/v1/alarms` | integer | Read | Client aggregation; define active as not closed/cleared with Dev 1 |
| Filter | Site / Zone | `site_code`, `zone_code` | device list | string | Read | API does not implement these query filters; filter returned page client-side or request enhancement |
| Zone card | Zone Summary | counts | `GET /api/v1/zones/{zone_code}/summary?site_code=...` | object | Read | 404 for unknown zone |

Empty state: show “No devices/alarms in this organization.” Stale state: compare current time to `last_seen_at` and `timeout_seconds`; do not invent an online value. API already projects missing status as offline.

## S1 Temperature & Humidity

| UI Component | Display Label | Source Field | API Endpoint | Data Type | Read/Write | Notes |
|---|---|---|---|---|---|---|
| Number card | Current Temperature | metric=`temperature`, `value` | `GET /devices/{id}/latest` | float | Read | Display °C; source unit is `Cel` |
| Number card | Current Humidity | metric=`humidity`, `value` | same | float | Read | Display %RH |
| Time-series chart | Temperature History | `points[].measured_at/value` | `GET /devices/{id}/telemetry?metric=temperature` | array | Read | Newest first |
| Time-series chart | Humidity History | same | metric=`humidity` | array | Read | No from/to query exists |
| Badge | Device Status | `current_state` | `GET /devices/{id}/status` | enum | Read | online/offline |
| DateTime | Last Seen | `last_seen_at` | same | datetime|null | Read | Show “Never seen” for null |

## S6 Door Monitoring

| UI Component | Display Label | Source Field | API Endpoint | Data Type | Read/Write | Notes |
|---|---|---|---|---|---|---|
| Badge | Current Door State | `latest_event.door_state` | `GET /devices/{id}/latest` | string|null | Read | open/closed; “Unknown” when null |
| Table | Door Event History | `events[]` | `GET /devices/{id}/events` | array | Read | event type, event/event receipt time, payload |
| Badge | Device Status | `current_state` | `GET /devices/{id}/status` | enum | Read | Do not confuse with door state |
| DateTime | Last Seen | `last_seen_at` | same | datetime|null | Read | “Never seen” when null |

## Alarm Management

| UI Component | Display Label | Source Field | API Endpoint | Data Type | Read/Write | Notes |
|---|---|---|---|---|---|---|
| Table | Alarms / History | `alarms[]` | `GET /api/v1/alarms` | array | Read | Occurrence and clear rows are separate |
| Badge | Severity | `severity` | same | enum | Read | Internal uppercase enum |
| Badge | Status | `status` | same | string | Read | open/cleared or lifecycle state |
| Text | Summary | `summary` | same | string | Read | Derived from `event.data.summary`, fallback event type |
| DateTime | Raised / Cleared | `raised_at`, `cleared_at` | same | datetime|null | Read | Do not equate cleared with closed |
| Drawer | Alarm Detail | list item fields | same | object | Read | PROPOSED projection; no detail endpoint |
| Timeline | Occurrence History | matching `episode_key` rows | same | array | Read | Client grouping; no episode endpoint |
| Action | Acknowledge | state=`acknowledged` | `POST /alarms/{alarm_key}/state` | object | Write | Dev 1 owns actor/version |
| Action | In Progress | state=`in_progress` | same | object | Write | Forward-only |
| Action | Close | state=`closed` | same | object | Write | Forward-only; not sensor clear |

## Device Management

| UI Component | Display Label | Source Field | API Endpoint | Data Type | Read/Write | Notes |
|---|---|---|---|---|---|---|
| Table | Devices | `devices[]` | `GET /api/v1/devices` | array | Read | Operational projection |
| Link | Profile | `profile_code` | list/config DocType | string | Mixed | Configuration owned by Dev 1 |
| Links | Site / Zone | codes | list/config DocTypes | string | Mixed | Names come from Dev 1 DocTypes |
| Badge | Connectivity | `current_state` | list/status | enum | Read | Data API only |
| DateTime | Last Seen | `last_seen_at` | list/status | datetime|null | Read | Data API only |
| Form | Device Configuration | Device API fields | `PUT /sync/devices/{device_id}` | object | Write | Save in Dev 1 then sync higher version |

## DocType storage boundary

Create/own DocTypes for Organization, Site, Zone, Device Profile (with child data points/event types), Device, Rule Configuration, Alarm business record/workflow, and integration sync metadata (`config_version`, last sync result). Read live from Data API: telemetry values/history, door events, online/offline state, last-seen timestamps, rule-runtime fields, occurrence payloads and outbox delivery internals.

Suggested UI-only fields such as `is_stale`, chart series, online counts and formatted units are computed and must not become authoritative persisted fields.
