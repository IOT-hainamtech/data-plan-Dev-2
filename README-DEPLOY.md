# nextWireless Dev 2 — Ubuntu deployment

This runbook deploys only the Dev 2 Data Plane. It does not deploy or modify
nextGRP/Dev 1. The deployment uses the isolated Compose project name
`nextwireless-dev2` by default and never exposes PostgreSQL on a host port.

Private source repository:
`https://github.com/hainamglobal/nextWirelessDataPlane`

Docker handoff release:
`https://github.com/hainamglobal/nextWirelessDataPlane/releases/tag/v2026.11.07`

Download the `linux-amd64` release archive from the private release, then verify
the SHA-256 value published with the release before extracting it:

```bash
sha256sum nextwireless-dev2-handoff-20261107-linux-amd64.tar.gz
tar -xzf nextwireless-dev2-handoff-20261107-linux-amd64.tar.gz
```

## Capacity and prerequisites

- Ubuntu 22.04/24.04 x86-64, Docker Engine 24+ and Docker Compose v2.
- At least 2 CPU cores, 2 GiB RAM allocated to this project and 10 GiB free disk.
- At least 1.5 GiB `MemAvailable` before initial startup.
- TCP 8883 reachable only from the trusted ESP32 LAN. The API defaults to
  `127.0.0.1:8002`; publish it through an approved reverse proxy if required.
- Server time synchronized with NTP.

The default core stack is capped at 1,376 MiB: PostgreSQL 640 MiB, Mosquitto
96 MiB, Data Plane 384 MiB and Data API 256 MiB. Optional Snapshot, Alarm
Outbox and Realtime workers are 192 MiB each. Running all three optional
workers plus the core is capped at 1,952 MiB. The optional nginx TLS proxy adds
64 MiB, so do not enable every optional profile simultaneously on a strict
2 GiB allocation without measuring host headroom. The `mock`, `demo` and
`mqtt-test` profiles are acceptance tools and are not part of that production
budget; stop optional integration workers before running them on a 2 GiB host.

Compose `mem_limit` and `cpus` are applied by Docker Engine. Verify them after
deployment with:

```bash
docker inspect -f '{{.Name}} memory={{.HostConfig.Memory}} cpu={{.HostConfig.NanoCpus}}' \
  $(docker compose -p nextwireless-dev2 -f compose.yaml -f compose.deploy.yaml ps -q)
docker stats --no-stream
```

## Initial configuration

```bash
cp deploy.env.example .env
chmod 600 .env
mkdir -p .secrets mosquitto/certs
```

Replace every required `CHANGE_ME` value. Use random, unique values for
`PG_PASSWORD`, `DP_INBOUND_TOKEN` and each MQTT account. Never put secrets in
Git or shell history.

Set `APP_IMAGE` to an immutable, tested company-registry tag or digest. The
default path pulls this image. Only when the operator explicitly approves a
server-side build should `DEPLOY_BUILD_LOCAL=true` be used; every Python
service still shares the resulting single image artifact.

For an offline release bundle, leave `DEPLOY_BUILD_LOCAL=false` and use the
pre-filled `APP_IMAGE_ARCHIVE` plus `APP_IMAGE_ARCHIVE_SHA256`. `deploy.sh`
verifies the checksum, loads the archive with `docker load`, and verifies that
the expected image tag exists before starting Compose.

Build an amd64 release bundle without pushing to any registry:

```bash
APP_IMAGE=nextwireless-dev2-data-plane:approved \
RELEASE_VERSION=1.0.0 TARGET_PLATFORM=linux/amd64 \
bash scripts/build-release-bundle.sh
```

The output under ignored `releases/` contains the image archive, Compose files,
deployment script, migrations/rollback, application source/tests, MQTT config,
sample environment and checksums. It excludes `.env`, `.secrets`, certificates,
private keys, database backups and firmware/NVS images.

Required ignored files:

- `.secrets/mqtt-broker.env`: `MQTT_DATA_PLANE_PASSWORD`,
  `MQTT_TH001_PASSWORD`, `MQTT_DR001_PASSWORD`.
- `.secrets/mqtt-data-plane.env`: `MQTT_USERNAME=data-plane` and its password.
- `mosquitto/certs/ca.crt`, `server.crt`, `server.key`.

Certificates must match the hostname/IP used by the devices. Keep the CA and
private key backups outside the repository. Re-running deployment reuses the
existing `mqtt_data`, `mqtt_ca` and `postgres_data` volumes; it does not rotate
passwords or certificates.

## One-command deployment

```bash
bash deploy.sh
```

The script validates Docker, required files, placeholders, `.env` permissions,
available RAM and merged Compose syntax. It initializes a volume containing
only the public MQTT CA, starts the four core services, applies additive DB
migrations through the existing Data Plane startup, and waits for health.
It is idempotent and never calls `down -v`, prune or volume deletion.

`DEPLOY_MODE=standalone` starts only the core path. For the integrated demo,
set `DEPLOY_MODE=integrated`, configure both Realtime and IF-05 high-water/time
guards, and explicitly arm both workers. The same command then starts core,
`realtime-worker` and `outbox-worker`. Missing credentials or guards stop the
script before Compose changes runtime. Set `ENABLE_SNAPSHOT_RECONCILIATION=true`
only when the 5-minute Dev 1 config contract and device IDs are approved.

The default services are:

| Service | Function | Host exposure |
| --- | --- | --- |
| `postgres` | TimescaleDB storage | none |
| `mqtt` | MQTT TLS broker | configured trusted LAN IP:8883 |
| `data-plane` | ingestion, Rules and status | none |
| `data-api` | read/sync API | loopback:8002 |

Optional profiles are deliberately not started by `deploy.sh`:

- `integration`: 5-minute snapshot/config reconciliation. Enable only after
  Dev 1 URL, identity mappings and credentials are confirmed.
- `outbound`: Alarm Outbox. It remains fail-closed unless delivery and the
  approved-ID/high-water guard are configured.
- `realtime`: new status/device-event delivery. It requires explicit armed
  high-water marks and Dev 1 credentials.

Resource totals by supported mode:

| Mode | Services | Container memory limit |
| --- | --- | ---: |
| Standalone | Core | 1,376 MiB |
| Integrated demo | Core + Realtime + Alarm Outbox | 1,760 MiB |
| Integrated + reconciliation | Above + Snapshot | 1,952 MiB |

The last combination leaves only 96 MiB inside a strict 2 GiB project budget
and is not recommended without measured headroom; use at least 2.5 GiB for all
three workers continuously.
- `api-tls`: nginx TLS termination using operator-provided certificates.
- `demo`, `mock`, `mqtt-test`: simulator/test-only; never use with physical
  demo data unless the test plan explicitly requires them.

The Device Event method defaults to:

`/api/method/nextwireless_dashboard.api.ingestion.receive_device_event`

Override only the method identifier with `DEV1_DEVICE_EVENT_METHOD`. Frappe
authentication, ACK validation, retry and outbox idempotency are unchanged.

## Operations

```bash
COMPOSE='docker compose -p nextwireless-dev2 -f compose.yaml -f compose.deploy.yaml'
$COMPOSE ps
$COMPOSE logs --tail=200 data-plane data-api mqtt postgres
$COMPOSE restart data-api
$COMPOSE stop
```

`stop` preserves containers and volumes. Do not use `down -v` when data must be
retained. To enable an optional worker, first complete its guard configuration,
then use the corresponding profile, for example:

```bash
$COMPOSE --profile realtime up -d --no-deps realtime-worker
```

## Backup and restore

Create a custom-format dump without binary shell redirection:

```bash
mkdir -p backups
$COMPOSE exec -T postgres pg_dump -U nw_user -d nextwireless -Fc \
  -f /tmp/nextwireless.dump
docker cp "$($COMPOSE ps -q postgres):/tmp/nextwireless.dump" \
  "backups/nextwireless-$(date -u +%Y%m%dT%H%M%SZ).dump"
sha256sum backups/nextwireless-*.dump
```

Always test the dump in a separate project/database before maintenance. Restore
only during an approved recovery window; stop application writers first, retain
the original volume, and follow TimescaleDB `timescaledb_pre_restore()` /
`timescaledb_post_restore()` procedures. A successful dump is not sufficient
until a test restore and row-count checks pass.

## Shared-server safety

- Always include `-p nextwireless-dev2` and both Compose files.
- Never prune Docker globally, restart the daemon, alter firewall rules, or
  touch containers/volumes outside this project.
- Bind MQTT only to the trusted private interface and firewall TCP 8883 to the
  ESP32 network. Keep Data API loopback-only unless a reviewed proxy is used.
- Monitor `docker stats`, disk usage, PostgreSQL volume growth and Outbox
  backlog daily during the 1–2 week demo.
- Prefer prebuilt, pinned images for final handoff. The current Compose can
  build locally; a company registry/tag should be supplied before deployment
  if building on the shared server is prohibited.

## Network requirements

No rule is created automatically by the deployment. The administrator must
approve the minimum paths below in the company firewall/security group:

| Direction | Port | Required for |
| --- | ---: | --- |
| ESP32 LAN → server private IP | TCP 8883 | MQTT over TLS |
| Local reverse proxy/admin → loopback | TCP 8002 | Data API; not Internet-facing |
| Approved clients → server private IP | TCP 8443 | Optional `api-tls` profile only |
| Containers → Dev 1 | TCP 443 | Integrated mode HTTPS POST/config fetch |
| Server → company registry | TCP 443 | Pulling `APP_IMAGE` and base images |
| Server → DNS/NTP infrastructure | site policy | Name resolution and host clock |

PostgreSQL 5432 is internal to the Compose network and must not be published.
Do not bind MQTT or Data API to `0.0.0.0` by default.

## Administrator preflight checklist

- [ ] Docker Engine and Compose v2 installed; user authorized for this project.
- [ ] At least 2 CPUs, 2 GiB project budget, 10 GiB disk and synchronized host time.
- [ ] Unique `COMPOSE_PROJECT_NAME`; no conflict with company containers/ports.
- [ ] `.env` is mode 0600 with no `CHANGE_ME` values.
- [ ] Per-device MQTT credentials and TLS CA/server certificate/key are present.
- [ ] Certificate SAN matches the private broker hostname/IP used by ESP32.
- [ ] `APP_IMAGE` is an approved immutable tag/digest and `docker login` grants pull-only access.
- [ ] MQTT TCP 8883 is restricted to the trusted ESP32 LAN; PostgreSQL is not published.
- [ ] Choose `DEPLOY_MODE=standalone` or complete all Realtime/IF-05 guards for `integrated`.
- [ ] For integrated mode, record new high-water IDs/timestamps; never reuse demo/backlog marks.
- [ ] Confirm available RAM for the selected mode and identify rollback image/database backup.
- [ ] Run `docker compose ... config --quiet`, then `bash deploy.sh`.
- [ ] Verify all selected services healthy, `docker stats`, API health and MQTT TLS ingestion.

## Rollback

Application rollback uses the previously approved image tags and a verified
database backup. Do not automatically roll back an additive schema after new
data has been written. If startup fails, stop only this project, retain all
volumes, collect logs, and choose either a forward fix or the reviewed migration
rollback based on the migration runbook.
