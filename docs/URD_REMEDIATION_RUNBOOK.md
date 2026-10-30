# URD v1.1 Dev 2 remediation runbook

## Safety boundary

Never run integration tests against the office/runtime database. Use a unique
Compose project name and disposable volumes. Do not start `simulator`,
`mock-nextgrp`, or `outbox-worker` in the office environment.

## Environment selection

Copy exactly one template from `config/environments` to a private env file and
supply secrets separately. `dev` and `lab` may use the Mock Bridge. `office`
requires real HTTPS endpoints and has no insecure HTTP allowance.

For HTTPS Data API, provide a certificate and key whose SAN matches the office
hostname/IP, then explicitly select `--profile api-tls`. The application HTTP
port must remain loopback-only; clients use `https://<host>:8443/api/v1/...`.

## Retention 90 days

Migration 016 creates only disabled control objects. Verify first:

```sql
SELECT * FROM telemetry_retention_config;
SELECT * FROM timescaledb_information.jobs WHERE proc_name='policy_retention';
```

After backup and explicit data-owner approval, enable with:

```sql
SELECT enable_telemetry_retention(90, 'approved-change-id');
```

Rollback future deletion with:

```sql
SELECT disable_telemetry_retention('rollback-change-id');
```

Disabling cannot restore chunks already deleted; restore requires the approved
database backup. Never enable this policy merely as part of migration testing.

## MQTT account retirement

Use `scripts/manage-mqtt-account.ps1 -Action revoke -Username <device-id>`. The script
removes the password entry and reloads Mosquitto with SIGHUP, without stopping
the broker. Verify within 60 seconds that the retired client disconnects and
cannot reconnect or publish. Registry `enabled=false` remains the independent
ingestion-side control. Record operator, device, UTC time and result. Full
automation from Dev 1 is optional in M1; the recorded manual procedure is not.

## Certificate operations

At least monthly, inspect the MQTT and Data API certificates and record subject,
SAN, serial, `notBefore`, `notAfter`, owner and planned renewal date. Renew before
the local policy threshold, validate CA/SAN from a separate IoT-VLAN client, and
roll one endpoint at a time. Never disable certificate verification.

## Physical and external acceptance still required

- TH-001: power, wiring, SHT31 CRC/error, 60-second telemetry, NTP before TLS.
- DR-001: polarity, debounce/chatter, no boot event, heartbeat, LWT/reconnect.
- TH-002: separate AT-11 identity and recorded physical/simulator method.
- Network: VLAN or approved exception, DHCP reservations, RSSI/loss, two NTP sources.
- Dev 1: config push, lifecycle callback, status reconciliation ACK, Alarm/Incident,
  idempotent occurrence/clear and real Outbox ACK.
