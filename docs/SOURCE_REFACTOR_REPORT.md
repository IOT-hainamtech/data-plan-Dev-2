# Source Structure Refactor Report

Date: 2026-10-22 (Asia/Saigon)
Branch: `refactor/project-structure`
Base: `feat/multi-site-data-plane` at `b206c4e`

## 1. Baseline

Before the refactor, Python runtime modules, tests and migration copies were colocated in `app/`.
Fresh database initialization used `db/*.sql`, while runtime migration used duplicated SQL files in
`app/` plus a special mount for migration 008. `api.py`, `ingestor.py` and `outbox_worker.py` were
both implementation modules and process entrypoints.

The existing uncommitted `docs/PROJECT_STRUCTURE_AUDIT.md` was preserved. Python compilation passed
before changes. Docker Desktop was initially unavailable, then started successfully so the complete
post-refactor container regression could be run. The previously committed stabilization evidence in
`DATA_PLANE_STABILIZATION_REPORT.md` remains historical context rather than a substitute for these
results.

## 2. Resulting Structure

```text
app/
  api/
    __init__.py
    application.py
  core/
    clock.py
    database.py
  ingestion/
    contracts.py
  integrations/
    nextgrp.py
  outbox/
    worker.py
  registry/
    repository.py
  rules/
    engine.py
  status/
    service.py
  ingestor.py
  migrate.py
  outbox_worker.py
  rule_engine.py
  simulator.py
  mock_nextgrp.py
  check.py
db/
  migrations/001_schema.sql ... 008_multi_site_registry.sql
tests/
  unit/
  integration/
  security/
  e2e/
firmware/
mosquitto/
scripts/
docs/
```

## 3. Why Modular Architecture

The application remains one codebase and one shared database, but responsibilities now have explicit
module boundaries. This preserves the proven Compose topology while reducing dependency on entrypoint
files. No dependency-injection framework or speculative domain layer was introduced.

Dependencies point from transport/entrypoints toward shared infrastructure and domain services:

```text
entrypoints/API -> core + registry/status/rules/outbox -> PostgreSQL/external adapter
```

There is no import from a domain package back into an entrypoint.

## 4. Files Moved

- `app/rule_engine.py` implementation → `app/rules/engine.py`; a compatibility module remains.
- `app/outbox_worker.py` implementation → `app/outbox/worker.py`; a process shim remains.
- `app/api.py` → `app/api/application.py`; `app/api/__init__.py` exports the FastAPI application,
  so `uvicorn api:app` remains valid and the file/package name conflict is avoided.
- `app/test_*.py` → `tests/unit`, `tests/integration`, `tests/security`, or `tests/e2e`.
- `db/001..008` → `db/migrations/001..008`.
- Duplicated `app/002..007.sql` copies were removed only after both Compose initialization and
  `migrate.py` were changed to the canonical directory.

## 5. Extracted Modules

| Module | Responsibility |
| --- | --- |
| `core.database` | Shared PostgreSQL connection factory |
| `core.clock` | M1-compatible naive UTC clock |
| `ingestion.contracts` | MQTT topic, timestamp and payload validation |
| `registry.repository` | Device/profile/point/event lookup |
| `status.service` | Presence transitions and timeout reconciliation |
| `rules.engine` | S1/S6 state machines, scoped rules, alarm/outbox writes |
| `outbox.worker` | Store, lease, retry, ACK validation and worker loop |
| `integrations.nextgrp` | Provisional external alarm HTTP adapter |
| `api.application` | FastAPI routes and development tenant dependency |

Business behavior, MQTT topics, payload contracts, IDs, rule timings, retry policy and demo seeds
were not intentionally changed.

## 6. Entrypoints

| Process | Entrypoint |
| --- | --- |
| Ingestion + timers | `python ingestor.py` |
| Data API | `uvicorn api:app --host 0.0.0.0 --port 8000` |
| Outbox | `python outbox_worker.py` |
| Simulator | `python simulator.py` |
| Mock nextGRP | `uvicorn mock_nextgrp:app --host 0.0.0.0 --port 8080` |
| Migration | `python migrate.py` |

Compatibility entrypoints preserve existing Compose commands and common imports.

## 7. Running the Refactored Project

Use the Quick Start in the repository README. The Docker application build context is now the
repository root with `app/Dockerfile`; the image receives `app/` and organized `tests/`. Both fresh
PostgreSQL initialization and runtime migration mount `db/migrations/` read-only.

Backend discovery is now:

```powershell
docker compose exec -T data-plane python -m unittest discover -s tests -v
```

## 8. README Changes

README was rewritten in concise professional English with the requested sections: overview,
features, architecture, stack, structure, prerequisites, Quick Start, environment, testing,
firmware, API/docs, current status and roadmap. It no longer presents a LAN IP as a universal
default, no longer repeats stale migration/test totals, and explicitly distinguishes the mock
tenant/nextGRP/hardware state from production readiness.

## 9. Validation

| Check | Before | After | Status |
| --- | --- | --- | --- |
| Python compilation | PASS | `python -m compileall -q app tests` | PASS |
| Unit tests | Environment lacked local `psycopg` | 15 tests in the application container | PASS |
| Integration tests | Previously verified on base branch | 32 passed with DB, HTTP and MQTT gates enabled | PASS |
| Fresh migrations | Previously verified on base branch | Migrations 001--008, idempotent rerun, 16 tests | PASS |
| MQTT security | Previously verified on base branch | 6 tests with ingestion checks enabled | PASS |
| Mock nextGRP | Previously verified on base branch | 1 test | PASS |
| End to end | Previously verified on base branch | LWT, S1 and S6: 3 tests | PASS |
| Compose/API | Docker initially unavailable | Compose config, services, DB check, health/devices/metrics/OpenAPI | PASS |
| Firmware Python contracts | Previously verified on base branch | 9 tests | PASS |
| ESP32 builds | Previously verified on base branch | `sht31` and `reed_door` | PASS |
| Firmware native C++ test | Previously verified on base branch | Host has no `gcc`/`g++` on `PATH` | ENVIRONMENT BLOCKED |

The final enabled suites contain 56 backend tests and 9 firmware Python contract tests: 65 passed,
0 failed and 0 skipped. A DB-only integration discovery reports two expected feature-gated skips:
the mock HTTP test requires `RUN_HTTP_TESTS=1`, and the cross-device MQTT ACL test requires
`RUN_MQTT_TESTS=1`. Both were enabled and passed in the final integration run. The isolated fresh
migration check additionally reran 16 relevant integration tests successfully; those repetitions are
not double-counted in the 65-test total.

The E2E suite exposed two pre-existing sources of nondeterminism: S6 assumed execution outside
business hours, and polling used the wall clock even when the host clock moved. The test now
temporarily snapshots/restores the S6 schedule, forces the outside-hours path, polls outbox delivery,
and uses a monotonic clock. Production rule logic, rule defaults and durable data are unchanged.

## 10. Remaining Risks

- `X-Org-Code` remains a development-only, forgeable context.
- The nextGRP adapter remains provisional until the real contract is signed.
- MQTT ACL lifecycle is still manual and not synchronized from the registry.
- Timestamp storage remains naive UTC for compatibility.
- `ingestor.py` is smaller but still owns transaction orchestration and MQTT lifecycle; further
  extraction should wait until current regression and nextGRP boundary design are stable.
- The native PlatformIO C++ contract test requires a host `gcc`/`g++`; both ESP32 cross-builds and
  the Python firmware contracts pass in the current environment.

## 11. Intentionally Not Refactored

- No database schema or existing migration content was changed.
- Rule S1/S6 and outbox timing/idempotency were not redesigned.
- Firmware protocol, network, TLS, firewall and LAN settings were not changed.
- SQL repositories were not abstracted behind generic interfaces; direct psycopg remains clear at
  the current project size.
- The application was not split into separate repositories or databases.

## 12. nextGRP Integration Impact

The new `integrations.nextgrp` boundary isolates the provisional HTTP adapter, while registry,
status, rules and outbox responsibilities are explicit. This makes replacement with the signed
nextGRP contract less likely to disturb MQTT ingestion or rule behavior. Production integration
still requires verified identity/RBAC, configuration synchronization, MQTT credential/ACL lifecycle
and formal ACK/replay semantics.
