# Dev1 / nextGRP integration boundary

## Implemented in this repository

- Additive migration `008_multi_site_registry.sql` for Organization, Site, Zone, Device Profile,
  profile points/events, device settings and scoped rules.
- Globally unique device identity with strict MQTT organization/site route validation.
- Profile-driven telemetry ranges and event mapping for the existing M1 profiles.
- Per-device durable runtime for device/profile/zone/site/organization rules.
- Read-only tenant-filtered device, telemetry, status, event and alarm API using the development
  `X-Org-Code` context.
- Automated multi-organization isolation coverage, while retaining the S1/S6 contracts.

## Pending Dev1 / nextGRP contract

No authoritative IF-01..IF-07 or nextGRP schema is present in this workspace, so this change does
not invent those payloads. Dev1 integration still needs an agreed contract for:

- authoritative organization/site/zone identifiers and lifecycle;
- device registration, reassignment, disable/revoke and profile assignment;
- profile and rule versioning, validation, synchronization and conflict ownership;
- authenticated organization claims and RBAC replacing `X-Org-Code`;
- status/alarm synchronization, ACK/error semantics and replay reconciliation;
- pagination, filtering, timestamps, audit fields and deletion/retention policy.

## Proposed ownership boundary

nextGRP should be the configuration/control-plane authority. The Data Plane should ingest a
versioned registry snapshot or idempotent change stream, validate it, and retain the last usable
configuration during temporary control-plane outages. The Data Plane remains authoritative for raw
telemetry/events, calculated device presence, rule runtime, alarm occurrences and outbox delivery
state. Exact endpoint names and payload fields are intentionally deferred until the Dev1 contract is
provided.
