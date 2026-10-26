# Dev 1 DocType Mapping

## Confirmed mapping

| Dev 2 Field | Dev 1 Suggested Field | Source | Destination | Transform | Required | Notes |
|---|---|---|---|---|---|---|
| `org_code` | `organization_code` | Organization API | Organization | direct | Yes | Stable external key |
| `name` | `organization_name` | Organization API | Organization | direct | Yes | Stored as `display_name` in Dev 2 |
| `version` | `config_version` | Org/Site/Zone/Profile/Rule | matching DocType | direct | Yes | >=1, monotonic |
| `site_code` | `site_code` | Site API | Site | direct | Yes | Unique with organization |
| `timezone` | `timezone` | Site API | Site | direct | Yes | default UTC |
| `zone_code` | `zone_code` | Zone API | Zone | direct | Yes | Unique with org/site |
| `profile_code` | `profile_code` | Profile API | Device Profile | direct | Yes | Globally keyed in Dev 2 |
| `data_points[]` | child table | Profile API | Profile Data Point | expand rows | No | Preserve exact code/type/channel/unit/range |
| `event_types[]` | child table | Profile API | Profile Event Type | expand rows | No | Preserve event code |
| `device_id` | `device_id` | Device API | Device | direct | Yes | Globally keyed in Dev 2 |
| `config_version` | `config_version` | Device API | Device | direct | Yes | API name differs from other resources |
| `connectivity_type` | `connectivity_type` | Device API | Device | direct | Yes | WIFI/LAN/4G/5G |
| `status` | `lifecycle_status` | Device API | Device | direct | Yes | Not online/offline |
| `rule_id` | `rule_id` | Rule API | Rule | direct | Yes | Stored as `rule_code` |
| `rule_type` | `rule_type` | Rule API | Rule | direct | Yes | Stored as `rule_kind` |
| `scope.type/ref` | scope type/link | Rule API | Rule | resolve link by type | Yes | DEVICE/PROFILE/ZONE/SITE/ORGANIZATION |
| `severity` | `severity` | Rule API | Rule | title-case outbound; internal DB uppercase mapping | Yes | Do not persist both without reason |
| `current_state` | `online_state` | Data API | live UI | direct | Read | Do not store as device lifecycle status |
| `metrics[]/points[]` | chart/card | Data API | live UI | select by metric | Read | Do not duplicate in DocType |
| `events[]` | door history | Data API | live UI | direct | Read | Do not duplicate in DocType |
| `event.data.summary` | `summary` | Bridge alarm | Alarm | direct | Yes | Confirmed by implementation |
| `alarm_key` | `external_alarm_key` | Bridge alarm | Alarm | direct | Yes | Public episode identity |
| `episode_key` | `episode_key` | Bridge alarm | Alarm | direct | Yes | Equal to alarm_key in M1 |
| `kind` | transition kind | Bridge alarm | Alarm Occurrence | direct | Yes | occurrence/cleared |
| `raised_at`/`cleared_at` | occurrence timestamp | Bridge alarm | Alarm Occurrence | select by kind | Conditional | Exactly one required by kind |
| `state` | `workflow_state` | lifecycle callback | Alarm | direct | Yes | acknowledged/in_progress/closed |
| `changed_by` | `modified_by_external` | lifecycle callback | Alarm History | direct | Yes | Actor reference |
| `event_id` | `idempotency_key` | Bridge events | Integration Inbox | direct/header | Yes | Must match Idempotency-Key |
| `ack_id` | `bridge_ack_id` | Bridge ACK | integration log | direct | Yes | Stable on duplicate |
| snapshot `resources[]` | configuration rows | Snapshot | DocTypes → Dev 2 | ordered expansion | Yes | Omission is not delete |

## DocType field recommendations

Dev 1 should store the full public API fields for the six configuration resources, plus local Frappe naming/workflow fields. It should store business-facing Alarm and Alarm Occurrence records received through the Bridge. Secret values must remain in the secret store; `credential_ref` is a reference only.

Do not store Dev 2 telemetry rows, status history, device events, rule runtime or outbox retry state as management-plane master data. Query those through the Data API.

## NEEDS_DEV1_CONFIRMATION

- Exact Frappe DocType and link field names; the suggested names above are not present in this repository.
- Whether public device IDs and profile codes remain globally unique.
- Whether Bridge creates one Alarm per `alarm_key` and child occurrences per transition.
- Whether lifecycle `in_progress` maps to assignment or a separate workflow action.
- Whether `event.data.summary` can remain English-only; source confirms the mapping, not localization policy.
- Tombstones/deletion policy and whether disabled/retired resources remain visible.
