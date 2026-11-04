# nextWireless M1 operator CLI

Run every command from the repository root on Windows PowerShell:

```powershell
.\scripts\nw.ps1 help
```

The CLI reads PostgreSQL through the existing Compose `postgres` container. It
does not require a local `psql`, Python environment, token, or database password.
Database inspection uses `psql -f -` through stdin and is SELECT-only. Database
timestamps remain UTC; displayed timestamps use `Asia/Ho_Chi_Minh` and format
`DD/MM/YYYY HH:mm:ss`.

## Command summary

| Command | Purpose | Side effects |
|---|---|---|
| `help` | Help and copy-paste examples | None |
| `status` | Compose, ESP32, Data API, ngrok and E2E Worker status | Read-only |
| `live` | Combined S1, S6 and Alarm screen | Read-only |
| `data` | Latest S1/S6 measurements | Read-only |
| `history` | S1 telemetry history | Read-only |
| `events` | S6 transition history and receive latency | Read-only |
| `raw` | Original JSON envelopes | Read-only |
| `export` | Export SELECT results to CSV | Writes only the chosen CSV |
| `alarms` | Occurrence, Outbox and ACK state | Read-only |
| `logs` | Sanitized service logs | Read-only |
| `start` | Start only core Compose services | Starts containers |
| `tunnel` | Reuse or start ngrok for Data API | May start ngrok |
| `arm` | Start guarded IF-05 physical-test worker | Can POST new Alarm rows after confirmation |
| `stop-e2e` | Stop only `nextwireless-if05-e2e` | Stops that one container |

## Observe the complete path

### Status

```powershell
.\scripts\nw.ps1 status
```

`ONLINE` requires both a stored online state and a `last_seen_at` within the
configured timeout. Missing evidence is reported as `UNKNOWN`, not PASS.

### Live screen

```powershell
.\scripts\nw.ps1 live
.\scripts\nw.ps1 live -IntervalMs 500
.\scripts\nw.ps1 live -Once
.\scripts\nw.ps1 live -Stream -Detail
.\scripts\nw.ps1 live -Stream -Detail -IntervalMs 500
```

The default refresh is 1000 ms; the minimum is 500 ms. This controls only screen
refresh. It never changes the ESP32 telemetry or heartbeat interval.

S6 state comes from the newest transition using
`device_events ORDER BY event_at DESC, id DESC`. A heartbeat can establish
freshness elsewhere, but an older snapshot never overwrites a newer OPEN/CLOSED
event on this screen.

`-Stream -Detail` is intended for acceptance evidence. It appends a complete S1
and S6 block every interval and never calls `Clear-Host`, so prior samples remain
scrollable. Each block contains device presence, measurements/state, quality,
measured/event and received timestamps, sequence, boot ID, `ts_suspect`, Rule
version/configuration, current Rule runtime status, latest occurrence, Outbox ID,
delivery state and Dev 1 ACK.

For an OPEN door it displays two independent durations:

- `Thời gian mở`: observation calculated from the newest `DOOR_OPENED.event_at`;
- `Rule Engine xác nhận`: duration calculated from
  `rule_runtime_state.state_changed_at`, together with the Rule active flag.

The first value is explicitly labelled as an estimate and is never treated as
proof that the 300-second Rule hold has fired. If a source field is absent, the
screen prints `--` instead of inventing a value. Use Ctrl+C to stop streaming.

### Latest device data

```powershell
.\scripts\nw.ps1 data -Limit 10
.\scripts\nw.ps1 data -Device TH-001 -Limit 20
.\scripts\nw.ps1 data -Device DR-001 -Follow
.\scripts\nw.ps1 data -Device DR-001 -Follow -IntervalMs 500
```

S1 output includes temperature, humidity, measured/received times, quality,
`ts_suspect`, sequence and boot ID. S6 output includes OPEN/CLOSED, transition,
event/receive times, MQTT/network receive latency, sequence and boot ID.

### S1 history

```powershell
.\scripts\nw.ps1 history -Device TH-001 -Metric temperature -Since 5m -Limit 100
.\scripts\nw.ps1 history -Device TH-001 -Metric humidity -Since 30m -Limit 200
.\scripts\nw.ps1 history -Device TH-001 -Metric all -Since 1h
.\scripts\nw.ps1 history -Device TH-001 -Metric all -Since 24h -Limit 1000
```

Valid periods are `5m`, `30m`, `1h`, and `24h`. Newest rows appear first.

### S6 events

```powershell
.\scripts\nw.ps1 events -Device DR-001 -Since 30m
.\scripts\nw.ps1 events -Device DR-001 -EventType DOOR_OPENED -Since 24h -Limit 100
.\scripts\nw.ps1 events -Device DR-001 -EventType DOOR_CLOSED -Since 1h
```

`Độ trễ (s)` is `received_at - event_at`. A large positive value indicates that
firmware buffering, Wi-Fi, MQTT, or ingestion should be investigated.

### Raw JSON

```powershell
.\scripts\nw.ps1 raw -Source telemetry -Device TH-001 -Limit 3
.\scripts\nw.ps1 raw -Source events -Device DR-001 -Limit 5
.\scripts\nw.ps1 raw -Source snapshots -Device DR-001 -Limit 5
```

The maximum raw limit is 100. Original UTC timestamps and original payload are
preserved; additional `_vn` fields are projections for operator convenience.

### CSV export

```powershell
.\scripts\nw.ps1 export -Type telemetry -Device TH-001 `
  -Start 2026-11-03T08:00:00+07:00 -End 2026-11-03T09:00:00+07:00 `
  -Path .\reports\TH-001-telemetry.csv

.\scripts\nw.ps1 export -Type events -Device DR-001 `
  -Start 2026-11-03T08:00:00+07:00 -End 2026-11-03T10:00:00+07:00 `
  -Path .\reports\DR-001-events.csv
```

Use an explicit UTC offset in `Start` and `End`. Export performs only SELECT and
never includes service credentials, API tokens, or Authorization headers.

### Alarm and Outbox

```powershell
.\scripts\nw.ps1 alarms -Limit 20
.\scripts\nw.ps1 alarms -Device TH-001
.\scripts\nw.ps1 alarms -Device DR-001 -Follow -IntervalMs 1000
```

The view joins `alarm_occurrences` and `alarm_outbox`, showing occurrence kind,
Rule/version, Outbox state, attempts, ACK, delivery time and episode.

### Logs

```powershell
.\scripts\nw.ps1 logs -Service data-plane -Tail 100
.\scripts\nw.ps1 logs -Service mqtt -Follow -Tail 50 -Filter ERROR
.\scripts\nw.ps1 logs -Service worker -Follow -Filter ACK
```

Valid services are `worker`, `data-plane`, `data-api`, and `mqtt`. When the named
physical E2E container exists, `worker` reads that container rather than an old
Compose service log. Common credential/header patterns are redacted. Docker UTC
timestamps are projected to Vietnam time.

## Runtime operations

### Start core services

```powershell
.\scripts\nw.ps1 start
```

This runs only `postgres`, `mqtt`, `data-plane`, and `data-api`. It does not select
the `demo`, `mock`, or `outbound` profiles. Existing healthy containers are not
explicitly restarted.

### Data API ngrok tunnel

```powershell
.\scripts\nw.ps1 tunnel
```

The command requires Data API health on `127.0.0.1:8002` first. It reuses an
existing port-8002 tunnel; otherwise it starts the installed ngrok executable in
a hidden window and reads the current URL from the local ngrok API. No temporary
URL is stored in source. Give the displayed URL to Dev 1 only when their Dashboard
is intended to read this Data API.

## IF-05 physical E2E ARM

`arm` is the only CLI command that can send data to Dev 1. It sends Alarm Outbox
rows only—never telemetry, status, or the existing backlog.

Dry-run preflight:

```powershell
.\scripts\nw.ps1 arm -Dev1BaseUrl https://<current-dev1-host> `
  -ConfirmText ARM -DryRun
```

Actual ARM requires explicit operator approval:

```powershell
.\scripts\nw.ps1 arm -Dev1BaseUrl https://<current-dev1-host> `
  -ConfirmText ARM
```

The command refuses to run unless:

- confirmation is exactly `ARM`;
- no other Outbox Worker is running;
- Dev 1 HTTPS ping returns HTTP 200;
- the supplied credential passes read-only `get_device_config` for `NW-IT-TH001`;
- all five Organization/Device/Rule mappings are confirmed and enabled;
- both runtime Rules and their mappings are enabled version 2.

It captures `MAX(alarm_outbox.id)` and PostgreSQL UTC time in one read-only
statement. The worker can claim only rows with both a greater ID and a later
creation timestamp, under `hainam`, the two physical devices, the two approved
rules, and same-version mappings. `BRIDGE_REQUIRE_202=false` accepts the confirmed
Frappe HTTP 200 ACK contract.

The Frappe `KEY:SECRET` is requested using `SecureString`, passed through process
environment without appearing in command arguments, and immediately removed from
the operator PowerShell environment. It is never written to source or a file.

Stop only the acceptance worker:

```powershell
.\scripts\nw.ps1 stop-e2e -DryRun
.\scripts\nw.ps1 stop-e2e
```

This does not stop PostgreSQL, MQTT, Data Plane, Data API, or ngrok and does not
modify pending Outbox rows. Existing pending row 956 requires a separate explicit
fixed-ID approval; the physical E2E window will never pick it up.

## Physical acceptance sequence

1. Confirm `status`, `live`, and `alarms` first.
2. Obtain approval, then execute `arm`.
3. S1: warm the physical SHT31-D above 30°C; observe TRIGGERED and ACK; let it
   cool below the Rule recovery condition and observe CLEARED.
4. S6: start CLOSED, open the physical MC-38 for at least 305 seconds; distinguish
   immediate OPEN status from the delayed Alarm; close and observe CLEARED.
5. Have Dev 1 confirm actual Dashboard display.
6. Run `stop-e2e` after acceptance.

Synthetic unit/integration fixtures prove software guards only. They are not
evidence of physical E2E acceptance.
