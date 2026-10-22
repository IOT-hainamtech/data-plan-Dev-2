# Dev 1 ↔ Dev 2 Integration v1.3 Report

## Scope and result

Dev 2 accepts the exact examples in `docs/contracts/nextwireless_m1_contract_v1_3.json`, maps them to the stable internal schema, persists configuration in PostgreSQL and exchanges status/alarm events with the development Mock Bridge. The contract remains `1.3-proposed`; no production nextGRP endpoint or credential has been used.

Implemented areas:

- seven authenticated, tenant-scoped inbound endpoints with URL/body identity, relationship and monotonic-version checks;
- organization/site/zone/profile/device/rule synchronization and alarm lifecycle callback;
- durable alarm and status outboxes, HTTP 202 ACK validation, delivery-event idempotency and bounded retry policy;
- stable episode identity for occurrence/cleared events and PostgreSQL-backed episode mapping;
- ordered, transactional snapshot reconciliation without deletion on omission;
- PostgreSQL-backed Mock Bridge inbox and snapshot, so ACK state survives process restart;
- authenticated IF07 reads with bounded result limits and organization/site/zone scoping.

## Database

Migration `009_dev1_integration_v1_3.sql` adds configuration versions/hashes, delivery identity, status outbox, episode mappings and alarm lifecycle. Migration `010_bridge_mock_durable_inbox.sql` adds development-only durable Bridge inbox/snapshot tables. Both are additive and idempotent. Fresh-schema and second-pass migration tests run in an isolated Compose project; the main PostgreSQL volume is not removed or reset.

## Verification

- Compile/import in Docker image: PASS.
- Full discovery with every feature gate enabled: 64 PASS, 0 FAIL, 0 SKIPPED.
- MQTT end-to-end coverage includes LWT/reconnect, S1 and S6; MQTT TLS/ACL includes live PostgreSQL ingestion.
- Isolated fresh migration/regression suite: 16 PASS; idempotency pass and required constraints PASS.
- Snapshot reconciliation: six resource kinds, reversed input order, tenant and stale-version protection PASS.

## Docker Mock E2E

```powershell
docker compose build data-plane data-api mock-nextgrp outbox-worker snapshot-worker
docker compose up -d postgres mqtt data-plane data-api mock-nextgrp outbox-worker snapshot-worker
docker compose --profile mqtt-test run --rm -e RUN_E2E_TESTS=1 mqtt-security-test python -m unittest -v tests.e2e.test_e2e_simulator
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-migrations-fresh.ps1
```

Create `.env` and `.secrets/*` from documented examples first. Never commit those files. HTTP is permitted only for the Compose mock; a real Bridge URL must use HTTPS.

## Known limits

The mock proves the transport and durable-inbox contract, not Frappe delivery. The production URL/token, final contract approvals, real hardware validation and production observability remain outside this implementation. Snapshot omission never deletes records. Dashboard APIs provide bounded `limit` queries rather than cursor pagination in M1.
