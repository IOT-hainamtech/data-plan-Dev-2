# Technical notes

- `telemetry` is a TimescaleDB hypertable on `measured_at`, using a unique key including the time dimension. `id` is an identity column but not a stand-alone primary key because Timescale unique indexes must include the partitioning time column.
- The demo continues to normalize all timestamps to **naive UTC** (`timestamp without time zone`) for compatibility with the Stage 2 Rule Engine. Production should clearly define UTC semantics and test timezone conversion.
- PostgreSQL replaces MySQL-specific `INSERT IGNORE` with `ON CONFLICT ... DO NOTHING` and `ON DUPLICATE KEY UPDATE` with `ON CONFLICT ... DO UPDATE`.
- JSON payloads are inserted as PostgreSQL `jsonb` using `psycopg.types.json.Jsonb`.
- The broker and API bind to localhost only on Windows; the container-internal MQTT listener remains anonymous and unencrypted. Not production-safe.
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
