# Multi-organization Data Plane design

## Identity and hierarchy

`device_id` remains globally unique. This deliberately preserves the existing MQTT ACL root,
deduplication keys, foreign keys, alarm keys and runtime state. A device belongs to exactly one
`Organization / Site / Zone` through the registry:

```text
organizations (org_code)
  sites (org_code, site_code)
    zones (org_code, site_code, zone_code)
      devices (globally unique device_id, profile_code)
```

Telemetry, events and status continue to store only `device_id`. Location is resolved by joining
the device registry, avoiding repeated organization/site/zone metadata in time-series rows. Moving
a device changes its current registry location; immutable historical placement would require a
future effective-dated assignment table.

## Device profiles and ingestion

`device_profiles`, `device_profile_points` and `device_profile_events` define accepted channels,
point types, canonical units, numeric ranges and event/state mappings. The ingestor resolves the
device from the full MQTT route `nw/v1/{org}/{site}/{device}/{channel}`, rejects a route whose
organization/site does not match the registry, and validates data from the assigned profile.
Payload numeric values are assumed to use the profile's canonical unit; schema version 1 does not
carry per-value units.

The M1 `TEMP_HUM_SENSOR` and `DOOR_SENSOR` contracts are seeded as data and remain backward
compatible. Adding a profile or device is a registry operation and does not require another
profile-specific branch in ingestion.

## Rule scopes

Rules support `DEVICE`, `PROFILE`, `ZONE`, `SITE`, and `ORGANIZATION`. Rule selection joins the
incoming device to its registry metadata. Runtime remains keyed by `(rule_code, device_id)`, so a
shared rule has independent hold/recovery/quiet-window state for every device. Existing M1 rules
are migrated as `DEVICE` scope without changing their behavior.

## Tenant boundary

Data endpoints require `X-Org-Code` and include `org_code` in registry authorization/filtering.
A device from another organization is returned as 404 to avoid identifier disclosure. Alarm lists
are joined through `devices` and filtered the same way. `/health` and `/outbox/metrics` remain
operator endpoints and are not tenant data APIs.

This header is only a local mock identity boundary. It is not authentication: a caller can forge
it. Production must replace it with verified nextGRP identity, organization claims, service-to-
service authentication and authorization/RBAC. PostgreSQL row-level security is not enabled in M1.

## Scale and failure boundaries

Indexes cover device location, rule scope and existing device/time-series access. Global device IDs
make topic authorization and deduplication deterministic. Current rule evaluation is synchronous in
the ingestion transaction and is suitable for M1-scale validation, not a high-cardinality rules
benchmark. Retention, partition sizing, effective-dated device moves, RLS, registry cache invalidation,
bulk provisioning and organization-level rate limits remain future production work.
