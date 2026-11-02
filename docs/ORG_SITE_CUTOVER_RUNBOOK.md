# Physical Organization/Site cutover

> **SUSPENDED:** Runtime cutover is not part of the current integration plan.
> Dev 1 uses the fixed adapter mapping documented in
> `docs/integration/DEV1_DATA_API_MINIMAL_MAPPING.md`. Do not execute this
> runbook unless a new, explicit migration approval is issued.

Target: `hainam/hq` → `NW-LOCAL-ORG-001/NW-LOCAL-SITE-001` for physical
devices `TH-001` and `DR-001`. Device IDs, profiles, zones and rule codes do
not change. Historical Alarm/Outbox payloads remain under their original org.

## Safety gates

1. Keep Outbox Worker, simulator and Mock Bridge stopped. Confirm the 27
   pending rows and occurrence IDs 947–950 with SELECT-only queries.
   The snapshot worker is independent of outbound Alarm delivery; if it is
   already running, leave it unchanged unless the approved deployment plan
   explicitly includes its environment update.
2. Back up PostgreSQL with the existing `scripts/backup-config.ps1` process and
   record the dump path/checksum outside Git. Do not continue without a usable
   restore artifact.
3. Review `db/manual/017_align_physical_org_site.sql`. It is deliberately not
   mounted into `/docker-entrypoint-initdb.d` and must never run automatically.
4. Activate the transition ACL (six exact publish topics per physical device),
   reload Mosquitto in an approved window, and prove old topics still work.
5. Generate NVS binaries from the two templates using existing secrets. Never
   commit the generated CSV/BIN. The new NVS is required because firmware builds
   MQTT topics from `org_code` and `site_code` stored in NVS.

## Coordinated cutover

Stop if any gate fails. During the maintenance window, pause physical publishing,
run the manual SQL with `psql -v ON_ERROR_STOP=1 -f`, verify both registry rows,
then provision the new NVS on both ESP32 devices and reconnect them. This order
avoids accepting new-topic messages before registry authorization. A brief ingest
pause is expected; no server/container restart is required by the SQL itself.

Set the runtime secret (not the tracked sample) to:

`DP_INBOUND_ALLOWED_ORGS=NW-LOCAL-ORG-001`

Restart/recreate Data API or snapshot-worker only in the separately approved
deployment window needed for environment changes. Do not enable outbound delivery.

Verify new MQTT messages, increasing sequences, registry site/zone, then call:

- `GET /api/v1/devices/TH-001/latest`
- `GET /api/v1/devices/DR-001/latest`

with the existing bearer token and `X-Org-Code: NW-LOCAL-ORG-001`. HTTP 200 on
old rows alone is not LIVE acceptance; require newly received timestamps after
the NVS cutover. Alarm history under `hainam` must remain visible only with the
old organization header, because occurrence payload org is immutable.

## Rollback

Pause both devices, run `db/manual/rollback_017_align_physical_org_site.sql`,
restore the previous ignored NVS artifacts, and reconnect on old topics. Keep the
transition ACL until old-topic LIVE data is proven. The rollback does not delete
target registry rows or change any telemetry, event, Alarm, episode or Outbox row.

Rollback changes registry and confirmed Organization/Site mapping only. Records
received after cutover keep their original `payload_json.org_code` and are not
reclassified. Consequently, after rollback the API may contain old-tenant Alarm
history plus immutable target-tenant Alarm history created during the cutover
window. Rollback does not undo external nextGRP changes, MQTT messages already
published, or NVS flashing; those require their explicit operational rollback.

Before production, run the isolated test from the repository root:

`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-org-site-cutover.ps1`

After stable operation and an approved observation period, remove the six old
`hainam/hq` ACL lines in a separate reviewed change.
