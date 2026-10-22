# Dev 1 Integration v1.3 API Matrix

Contract source: `docs/contracts/nextwireless_m1_contract_v1_3.json`. Version `1.3-proposed` remains a proposal.

| Interface | Data Plane endpoint | Auth/tenant | Persistence | Status |
|---|---|---|---|---|
| IF00 | `PUT /api/v1/sync/organizations/{org_code}` | Bearer + `X-Org-Code` | PostgreSQL | Mock-verified |
| IF00 | `PUT /api/v1/sync/sites/{org_code}/{site_code}` | Bearer + `X-Org-Code` | PostgreSQL | Mock-verified |
| IF00 | `PUT /api/v1/sync/zones/{org_code}/{site_code}/{zone_code}` | Bearer + `X-Org-Code` | PostgreSQL | Mock-verified |
| IF01 | `PUT /api/v1/sync/devices/{device_id}` | Bearer + `X-Org-Code` | PostgreSQL | Mock-verified |
| IF02 | `PUT /api/v1/sync/profiles/{profile_code}` | Bearer + `X-Org-Code` | PostgreSQL | Mock-verified; profile is globally keyed |
| IF03 | `PUT /api/v1/sync/rules/{rule_id}` | Bearer + `X-Org-Code` | PostgreSQL | Mock-verified |
| IF04 | `POST /internal/events/status` | Bridge Bearer | durable mock inbox | Mock-verified, HTTP 202 |
| IF05 | `POST /internal/events/alarm` | Bridge Bearer + idempotency key | durable mock inbox | Mock-verified, HTTP 202 |
| IF06 | `POST /api/v1/alarms/{alarm_key}/state` | Bearer + `X-Org-Code` | PostgreSQL | Mock-verified |
| Snapshot | `GET /internal/snapshot` | Bridge Bearer | PostgreSQL-backed mock | Mock-verified |
| IF07 | health/latest/telemetry/status/events/alarms/zone summary | Bearer + `X-Org-Code` except health | PostgreSQL read-only | Mock-verified |

Sync responses distinguish `applied` and `duplicate`; stale versions and same-version/different-payload requests return 409. URL/body identity, parent relationships, enums and tenant ownership are validated. Zone summary requires `site_code` because a zone code is not globally unique.

The public contract vocabulary is mapped at the API boundary to existing database names. The database schema is deliberately not renamed.
