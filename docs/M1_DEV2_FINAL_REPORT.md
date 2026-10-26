# nextWireless M1 Dev 2 Final Report

Status date: 2026-10-23  
Branch: `feat/dev1-integration-v1-3`

## Executive result

The Dev 2 Mock integration path is operational end to end: Simulator -> MQTT TLS/ACL -> Data Plane -> PostgreSQL -> S1/S6 Rule Engine -> durable alarm/status outbox -> Mock Bridge. Data API contract v1.3 and alarm-state callbacks remain operational. Real Bridge activation is deliberately pending credentials and final contract confirmation.

## Verification matrix

| Area | Result | Evidence / remaining issue |
|---|---|---|
| MQTT TLS, authentication, ACL, QoS 1, LWT | PASS | Live broker tests passed; broker healthy with zero restart loop |
| Simulator ingestion | PASS | New TH-001 telemetry and DR-001 door events persisted after rebuild |
| S1 threshold/duration/hysteresis/clear/quiet | PASS | Pure state-machine, DB integration and E2E tests |
| S6 schedule/hold/open/close/duplicate | PASS | Unit, DB integration and E2E tests |
| Disabled rules | PASS by query behavior | Rule SQL explicitly requires `enabled=TRUE`; `NW-TEST-R01` therefore does not fire |
| Alarm/status Bridge contract | PASS (Mock) | 202 ACK, duplicate, conflict, invalid body, wrong/missing auth, offline/recovery tested |
| Durable outbox retry/restart | PASS | ACK validation, retry, terminal failure and expired lease recovery tested |
| Snapshot reconciliation | PASS (Mock) | Dependency ordering, version protection and tenant rejection tested; omission performs no deletion |
| Data API v1.3 | PASS | 8/8 live API/Mock tests |
| Dashboard APIs | PASS | Tenant-filtered devices, zone summary, telemetry, events and alarms are implemented |
| Real Bridge | BLOCKED | Outbound token and final contract confirmation are absent |
| Physical firmware/hardware | VERIFICATION PENDING | Simulator contracts pass; no physical ESP32 run in this session |

## Changes made in this validation

- Fixed Mosquitto startup script line endings and pinned shell scripts to LF with `.gitattributes`.
- Corrected Paho exception handling to use the installed Paho 2.x exception location.
- Made Simulator organization/site/device identity configurable while retaining demo defaults and legacy credential names.
- Added strict alarm/status request models to the development Mock Bridge.
- Added deterministic alarm summaries without changing rule thresholds or transition logic.
- Made Bridge URLs configurable in Compose while retaining Mock endpoints as defaults.
- Added black-box Bridge contract tests for authentication, validation, duplicates, conflict and status recovery.

## Test totals

Executed validation groups:

- Unit tests: 15 PASS, 0 FAIL, 0 SKIPPED.
- Mock Bridge contract tests: 6 PASS, 0 FAIL, 0 SKIPPED.
- PostgreSQL ingestion/Multi-Site/outbox/rule/status group: 30 PASS, 0 FAIL, 1 SKIPPED. The skipped MQTT ACL class was feature-gated because that container intentionally had no simulator credentials; it was rerun in the MQTT test service and passed.
- MQTT security + E2E + the skipped ACL case: 10 PASS, 0 FAIL, 0 SKIPPED.
- Dev 1 contract v1.3: 8 PASS, 0 FAIL, 0 SKIPPED.

The temporary E2E setup error caused by running without simulator credential variables was corrected by rerunning the suite in the intended `mqtt-security-test` service; it is not counted as a product failure. No failing assertion was ignored.

## Runtime state

`postgres`, `mqtt`, `data-plane`, `data-api`, `mock-nextgrp`, `outbox-worker`, `snapshot-worker`, and `simulator` are running. MQTT and Mock Bridge are healthy. Recent logs show successful TLS connections, saved telemetry/events, HTTP 202 ACKs, zero pending/failed outbox items at the observed metrics point, and successful empty Mock snapshot reconciliation.

No Docker volume was deleted or reset. Existing PostgreSQL configuration and telemetry were preserved.

## Configuration audit

Read-only inspection confirmed the Dev 1 resources `NW-TEST-TH`, `NW-TEST-DOOR`, `NW-TEST-TH001`, `NW-TEST-DR001`, `NW-TEST-DR003`, `NW-TEST-R01`, and `NW-TEST-R02` with version/hash metadata. The profile point definitions are swapped and R02 uses a non-canonical event type; see `DEV1_INTEGRATION_HANDOFF.md`. These records were not modified.

## Remaining risks

- The Real Bridge uses plain HTTP on a LAN address. This requires explicit approval and network controls; HTTPS remains preferred.
- Real Bridge ACK/schema behavior has not been revalidated automatically with the final token.
- Snapshot deletion semantics remain intentionally conservative: missing resources are retained.
- The simulator proves protocol behavior, not sensor electrical reliability or Wi-Fi conditions on physical ESP32 hardware.
- Device/profile/rule configuration quality remains an upstream Dev 1 responsibility; malformed but schema-valid combinations can prevent expected alarms.

## DEV2 DONE / DEV1 ACTION REQUIRED / BLOCKED / VERIFICATION PENDING

- **DEV2 DONE:** secured ingestion, rule execution, durable delivery, Mock Bridge, tenant APIs, callback state machine and regression coverage.
- **DEV1 ACTION REQUIRED:** resend corrected `NW-TEST-*` profiles/rules at higher versions and provide Bridge credentials/final schemas.
- **BLOCKED:** unattended Real Bridge and Real snapshot activation.
- **VERIFICATION PENDING:** controlled Real Bridge test and physical S1/S6 devices.

## Real Bridge readiness update — 2026-10-23

### Current result

| Check | Result | Evidence |
|---|---|---|
| TCP `192.168.1.83:8081` | PASS | TCP connection established |
| `GET /health` | FAIL | Request timed out after TCP connect |
| Runtime outbound token | BLOCKED | `BRIDGE_OUTBOUND_TOKEN` is absent from local `.env`; value was never displayed |
| Auth/schema/ACK preflight | NOT RUN | Full preflight correctly refused because the token is absent |
| Outbox destination | MOCK | Alarm/status URLs resolve to `mock-nextgrp:8080` and `BRIDGE_MODE=mock` |
| Historical replay risk | CONTROLLED | 142 alarm and 86 status rows are already `DELIVERED`; no pending/in-flight rows were found |
| Snapshot destination | MOCK | Snapshot has a separate Mock token and was not switched |

Additional Real-readiness regression: 7 Outbox unit tests and 7 PostgreSQL outbox integration tests passed after adding mode validation, unified credentials and the delivery cutoff.

The worker now uses `BRIDGE_OUTBOUND_TOKEN` consistently for both alarm and status. `BRIDGE_MODE=real` refuses Mock URLs or a missing/Mock token. `BRIDGE_DELIVER_NOT_BEFORE` prevents rows created before the controlled activation timestamp from being claimed, even if an old row later becomes pending.

### Safe token installation and full preflight

Stop delivery before editing configuration:

```powershell
docker compose stop outbox-worker
```

Edit the ignored local `.env` directly. Do not paste the token into a terminal command, chat, log or tracked file:

```dotenv
BRIDGE_MODE=mock
BRIDGE_ALARM_URL=http://192.168.1.83:8081/internal/events/alarm
BRIDGE_STATUS_URL=http://192.168.1.83:8081/internal/events/status
BRIDGE_OUTBOUND_TOKEN=<set locally; do not commit>
BRIDGE_ALLOW_INSECURE_HTTP=true
```

Leave these Snapshot values on Mock:

```dotenv
BRIDGE_SNAPSHOT_URL=http://mock-nextgrp:8080/internal/snapshot
BRIDGE_SNAPSHOT_TOKEN=mock-development-token
```

Run the redacting preflight. It loads `.env` itself and never prints the token:

```powershell
python scripts/bridge_preflight.py --base-url http://192.168.1.83:8081
```

Full preflight deliberately creates uniquely identified demo status/alarm occurrence events and repeats the alarm to validate stable duplicate ACK behavior. Do not activate the worker unless TCP, health, auth, schema, HTTP 202 and ACK checks all pass.

### Controlled Mock -> Real activation

After full preflight passes, record the current UTC timestamp and set it in `.env`, for example:

```dotenv
BRIDGE_MODE=real
BRIDGE_DELIVER_NOT_BEFORE=2026-10-23T10:30:00Z
```

The cutoff must be the real activation time and must include `Z` or another timezone. Then recreate only the worker:

```powershell
docker compose up -d --no-deps --force-recreate outbox-worker
docker compose logs --since 2m outbox-worker
```

Confirm its startup log reports `BRIDGE_MODE=real`, host `192.168.1.83:8081`, and the expected cutoff. Generate only new controlled demo events after that cutoff. Verify HTTP 202, matching `event_id`/`alarm_key`, non-empty `ack_id`, automatic outbox delivery, duplicate behavior, occurrence/cleared pairing, and status offline/recovery. Do not infer nextGRP UI success from an HTTP ACK; Dev 1 must provide UI evidence.

### Immediate Real -> Mock rollback

Stop the worker first so no claim changes destination mid-flight:

```powershell
docker compose stop outbox-worker
```

Set the local `.env` values back to:

```dotenv
BRIDGE_MODE=mock
BRIDGE_ALARM_URL=http://mock-nextgrp:8080/internal/events/alarm
BRIDGE_STATUS_URL=http://mock-nextgrp:8080/internal/events/status
BRIDGE_OUTBOUND_TOKEN=mock-development-token
BRIDGE_DELIVER_NOT_BEFORE=
```

Then recreate only the worker and verify the target host in its redacted startup log:

```powershell
docker compose up -d --no-deps --force-recreate outbox-worker
docker compose logs --since 2m outbox-worker
```

Do not reset delivery states or manually replay failed rows during rollback. Investigate each Real delivery result first.

### Final acceptance still blocked

- Dev 1 must make `/health` responsive or provide the confirmed health path/expected behavior.
- Dev 1 must provide/install the matching inbound bearer token through a private channel.
- The malformed `NW-TEST-TH`, `NW-TEST-DOOR`, `NW-TEST-R01` and `NW-TEST-R02` configuration must be corrected and resynchronized at higher versions before it is used for S1/S6 acceptance.
- Real automatic occurrence, cleared, duplicate, retry, offline and recovery delivery remains unexecuted until the preflight passes.
- Snapshot stays on Mock until its Real response schema, authentication and omission semantics are separately verified.

## Dev 2 independent completion update — 2026-10-24

All work in this section kept `BRIDGE_MODE=mock`; no request was sent to the Real Bridge.

### E2E Mock evidence

The automated E2E suite now relies on the separately running Outbox Worker service rather than calling `OutboxWorker.run_once()` for its primary delivery assertions.

| Step | Evidence | Result |
|---|---|---|
| S1 MQTT publish | Six TH-002 QoS 1 samples covering the 300-second logical hold | PASS |
| Ingestion/storage | Six reports persisted with the test boot ID | PASS |
| S1 occurrence | `S1_TH002_TEMP_HIGH` occurrence and outbox row created | PASS |
| S1 cleared | Three recovery samples covering the 120-second clear hold; cleared row created | PASS |
| Automatic delivery | Running Outbox Worker changed both rows to `DELIVERED` | PASS |
| HTTP/ACK | Mock Bridge logged HTTP 202; rows contain non-empty ACK IDs | PASS |
| Duplicate protection | The occurrence test row was replayed once; attempt changed to 2, ACK ID stayed identical, Mock inbox count stayed 1 | PASS |
| S6 | DR-001 `DOOR_OPENED` traversed MQTT, `device_events`, rule engine, outbox and HTTP 202 ACK | PASS |
| LWT/reconnect | Offline and online status transitions persisted and delivered | PASS |

E2E result: 3 PASS, 0 FAIL after correcting one test-only UUID/varchar comparison.

### Dashboard Data API

Existing endpoints were retained and extended rather than duplicated:

- `/devices`, `/telemetry`, `/events`, and `/alarms` now return `total`, `limit`, and `offset`.
- `/devices/{device_id}/latest` now includes the latest event and projected `door_state` when available.
- `/alarms` now includes `raised_at`, `summary`, and business `status`, while retaining delivery metadata.
- Tenant filtering remains mandatory and Multi-Organization isolation was rerun.

Dashboard HTTP tests: 4 PASS. Multi-Organization regression: 2 PASS. The verified consumer documentation is `docs/integration/DEV1_DASHBOARD_API_GUIDE.md`.

### Retry and recovery

Mock Bridge was stopped while Simulator LWT generated two new status events. Both rows remained durable while delivery was unavailable. After Mock recovery:

- each row was delivered on attempt 2;
- each recorded one delivery failure followed by HTTP 202;
- each `event_id` appeared exactly once in `bridge_event_inbox`;
- two subsequent online recovery events delivered on attempt 1.

Policy tests confirm network errors, timeouts, invalid ACK and HTTP 500 are retryable. HTTP 401 and 422 are terminal because they indicate credential/contract errors that retries cannot repair. Outbox unit result: 8 PASS.

### Snapshot Mock

The v1.3 integration test now stores a six-resource fixture through Mock Bridge HTTP, fetches it with `SnapshotClient`, and reconciles Organization -> Site -> Zone -> Profile -> Device -> Rule. It verifies:

- all six resources reconcile in dependency order;
- every stored resource has `config_version >= 1` and a 64-character `payload_hash`;
- Organization v2 cannot be overwritten by stale v1;
- omission of Device/Rule from a later snapshot does not delete them;
- forbidden tenant content is rejected;
- the Mock snapshot is reset to an empty fixture after the test.

Contract/Snapshot result: 8 PASS.

### Current operational state

Outbox remains on Mock. After recovery testing all observed alarm/status rows are `DELIVERED`; no pending or failed rows remain. Simulator was restarted after the controlled tests. PostgreSQL volumes and synchronized Dev 1 configuration were not reset or modified manually.

### Still owned or blocked by Dev 1

- Correct and resynchronize the swapped `NW-TEST-TH`/`NW-TEST-DOOR` Profile definitions at higher versions.
- Enable/correct `NW-TEST-R01` and canonicalize `NW-TEST-R02` to `DOOR_OPENED` where required.
- Provide the Real Bridge bearer token and a responsive/confirmed health endpoint.
- Confirm Dashboard business labels/workflow and provide UI evidence; Dev 2 only proves API data and Bridge ACK behavior.
- Confirm Real Snapshot response/auth/deletion semantics before Snapshot leaves Mock.

## Final regression and handoff readiness — 2026-10-24

This section supersedes earlier incremental test counts. The complete current repository suite was divided into two non-overlapping runs covering all 14 `test_*.py` modules.

| Run | Scope | PASS | FAIL | SKIP |
|---|---|---:|---:|---:|
| 1 | Unit, ingestion/DB integration, MQTT TLS/auth/ACL, Rule Engine, Outbox, Bridge Contract and E2E | 64 | 0 | 0 |
| 2 | Dev 1 contract, Snapshot Mock and Dashboard API | 12 | 0 | 0 |
| **Total** | **All current test modules** | **76** | **0** | **0** |

Final runtime checks:

- `docker compose config --quiet`: PASS.
- PostgreSQL, MQTT and Mock Bridge report healthy; all eight required services are running.
- Data API `/api/v1/health`: `UP`.
- Outbox remains `BRIDGE_MODE=mock`; alarm/status target `mock-nextgrp:8080`.
- Snapshot remains on `mock-nextgrp:8080` and its fixture store was reset to zero resources.
- Current queue state after regression: alarm and status rows are all `DELIVERED`; no `PENDING` or `FAILED` rows.
- The single historical Data Plane restart count is stable and is not a restart loop.
- Expected warning logs were produced only by deliberate recovery/security tests (Mock unavailable, invalid CA and abrupt MQTT disconnect).

Read-only blocker verification still shows the Dev 1-owned `NW-TEST` resources at version 1: temperature/humidity remain on `NW-TEST-DOOR`, `door_state` remains on `NW-TEST-TH`, neither Profile has event definitions, `NW-TEST-R01` remains disabled, and `NW-TEST-R02` still uses `chống cháy`. `BRIDGE_OUTBOUND_TOKEN` is still absent locally.

**DEV2_READY_FOR_REAL_INTEGRATION: PASS**, meaning Dev 2 source, Mock E2E, durability, API, security and operational configuration are ready for a controlled Real Bridge preflight. This does not mean Real integration or nextGRP UI acceptance has passed. Activation remains blocked until Dev 1 supplies the token/health confirmation and corrects the synchronized `NW-TEST` Profile/Rule configuration.

