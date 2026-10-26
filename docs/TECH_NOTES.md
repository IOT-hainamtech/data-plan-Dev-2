# Technical notes

- `telemetry` is a TimescaleDB hypertable on `measured_at`, using a unique key including the time dimension. `id` is an identity column but not a stand-alone primary key because Timescale unique indexes must include the partitioning time column.
- The demo continues to normalize all timestamps to **naive UTC** (`timestamp without time zone`) for compatibility with the Stage 2 Rule Engine. Production should clearly define UTC semantics and test timezone conversion.
- PostgreSQL replaces MySQL-specific `INSERT IGNORE` with `ON CONFLICT ... DO NOTHING` and `ON DUPLICATE KEY UPDATE` with `ON CONFLICT ... DO UPDATE`.
- JSON payloads are inserted as PostgreSQL `jsonb` using `psycopg.types.json.Jsonb`.
- Mosquitto exposes only TLS port 8883 and binds localhost by default. Optional LAN
  binding is explicit through `MQTT_BIND_IP` and must be paired with the restricted
  Private-profile firewall rule documented in `hardware/MQTT_LAN.md`. Anonymous access is disabled;
  Data Plane and each simulator device use separate generated credentials. Static
  ACLs limit devices to their own topic root. Development CA keys, server keys and
  runtime credentials are generated locally and ignored by Git.
- The PostgreSQL volume is separate from the existing MariaDB volume. No automatic transfer of old alarms/telemetry.
- No standalone URD document is present in this workspace. The M1 S1/S6 behavior
  is implemented from the supplied acceptance criteria and remains configurable
  in PostgreSQL.
- `rule_runtime_state` is durable across process restarts. Alarm trigger/clear
  occurrences and their outbox rows are written in the ingestion/timer transaction.
- The Outbox Worker uses a committed PostgreSQL lease and `SKIP LOCKED`. Delivery
  is therefore at-least-once; the receiver must honor `Idempotency-Key`. A crash
  after remote ACK and before the local ACK update intentionally causes a safe
  replay rather than message loss.
- The bundled nextGRP endpoint and ACK document are provisional mocks. HTTPS is
  mandatory in the adapter unless `NEXTGRP_ALLOW_INSECURE_MOCK=true` is explicitly
  set for local testing.
- Timescale image `timescale/timescaledb:2.30.2-pg17` is pinned as observed on Docker Hub in October 2026; upgrade intentionally after tests rather than use `latest`.
- Static password/ACL files can be reloaded with SIGHUP. Revocation blocks future
  authentication after reload, but an already connected physical device must also
  have its session terminated operationally. Evaluate Mosquitto Dynamic Security
  for centralized immediate session revocation.
- `firmware/` is an ESP-IDF/PlatformIO hardware skeleton. Secrets are provisioned
  through NVS and the broker CA is embedded separately from source. The repository
  still has no authoritative URD v1.1 document, and no physical ESP32/SHT31/MC-38
  validation has been performed yet.
- Migration `005_urd_alignment.sql` records telemetry reports idempotently by
  `(device_id, boot_id, seq)`, including top-level SHT31 `quality=error` without fabricated
  measurements. DR-001 status heartbeats reconcile missed door transitions.
- Migration `008_multi_site_registry.sql` keeps `device_id` globally unique and adds the
  Organization/Site/Zone registry, data-driven profile contracts and five rule scopes. Tenant data
  API calls use a development-only `X-Org-Code` header; verified identity/RBAC remains a Dev1 task.
- A device belongs to exactly one Organization/Site/Zone through the current registry. Telemetry,
  events and status store the globally unique `device_id` and resolve current location by joining
  the registry, avoiding duplicated location fields in time-series rows. Moving a device changes
  its current location; preserving historical placement would require a future effective-dated
  assignment model.
- Device profiles define accepted channels, point types, canonical units, numeric ranges and event
  mappings. MQTT ingestion validates the complete `nw/v1/{org}/{site}/{device}/{channel}` route
  against the assigned registry and profile. Schema version 1 assumes values already use the
  profile's canonical unit.
- Rules may target `DEVICE`, `PROFILE`, `ZONE`, `SITE` or `ORGANIZATION`, while runtime state remains
  keyed by `(rule_code, device_id)` so shared rules have independent device state. PostgreSQL row
  level security is not enabled in M1; verified service identity and organization claims must
  replace the forgeable development header before production.
- The runtime remains one codebase with explicit modules for core infrastructure, ingestion,
  registry, status, rules, outbox, integrations and API. Entrypoints depend on those modules rather
  than the reverse. Migrations `001`–`010` have one canonical source under `db/migrations/`.
- Current synchronous rule evaluation is appropriate for M1 validation, not proof of high-scale
  capacity. Retention, partition sizing, effective-dated moves, registry-cache invalidation, bulk
  provisioning, tenant rate limits, backup/restore and scale benchmarks remain production work.
