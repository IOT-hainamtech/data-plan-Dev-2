# ESP32 First Boot and Hardware Acceptance Test

Perform one board at a time so device identity, COM port and wiring cannot be mixed.

## Pre-flight

| Check | TH-001 / S1 | DR-001 / S6 |
|---|---|---|
| PlatformIO environment | `sht31` | `reed_door` |
| NVS image | `firmware/config/nvs.TH-001.bin` | `firmware/config/nvs.DR-001.bin` |
| Application binary | `firmware/.pio/build/sht31/firmware.bin` | `firmware/.pio/build/reed_door/firmware.bin` |
| Device/profile | TH-001 / TEMP_HUM_SENSOR | DR-001 / DOOR_SENSOR |
| Pins | 3V3/GND, SDA21, SCL22 | GPIO27 and GND only |

Before powering: inspect shorts/polarity, verify no secret file is staged by Git, confirm broker LAN IP/certificate SAN, and confirm Dev 1 registry IDs. If Dev 1 uses different IDs, update Organization/Site/Device/Profile plus MQTT account/ACL and NVS consistently; never alias an unmatched device only in firmware.

For the current LAN, both NVS images must use `mqtts://192.168.1.40:8883`, and the server certificate must contain SAN `IP Address:192.168.1.40`. Complete the Administrator firewall step in `ESP32_MQTT_LAN_SETUP.md` before expecting another LAN host to connect.

## Flash and monitor

```powershell
.\firmware\.venv\Scripts\pio.exe device list
# Run the matching build/upload commands from the S1 or S6 guide.
.\firmware\.venv\Scripts\pio.exe device monitor -d firmware -p COMx -b 115200
```

Expected sequence: configuration accepted; correct device/sensor logged; Wi-Fi gets an IP; NTP establishes UTC; TLS MQTT connects; retained online birth publishes; periodic telemetry/status follows. Secrets must never appear in serial output.

## Functional acceptance

S1:

1. Confirm configured address log is 0x44 or 0x45.
2. Wait for at least three 60-second samples.
3. Check plausible temperature/humidity and no CRC/read warnings.
4. Warm the sensor gently and confirm temperature changes; do not use condensation or direct heat.

S6:

1. Record initial state with magnet present and absent.
2. If inverted, change NVS `door_closed_lvl`; do not patch code.
3. Cycle magnet slowly ten times. Expect one OPEN and one CLOSED event per cycle.
4. Wiggle near the transition point to confirm debounce prevents event storms.

Backend checks:

```powershell
docker compose logs -f mqtt data-plane
docker compose exec -T data-plane python check.py
```

Verify exact topics, device IDs, increasing sequence numbers, one stable `boot_id` per boot, UTC timestamps, QoS/PUBACK behavior, telemetry/event rows and current status. Disconnect power without a clean MQTT shutdown and verify retained LWT/offline; reconnect and verify online birth/recovery.

## Failure isolation

| Symptom | Likely cause / action |
|---|---|
| No COM port | Install CP210x/CH340 driver; change data-capable USB-C cable/port |
| Upload timeout | Hold BOOT during connection; close Serial Monitor; verify selected COM |
| Configuration rejected | Generate and flash correct NVS image at 0x9000 |
| Wi-Fi never connects | Use 2.4 GHz, verify WPA2 credentials/signal/client isolation |
| NTP fails | Check gateway/DNS/UDP 123; TLS intentionally does not start with invalid time |
| TLS certificate error | Regenerate cert for actual LAN IP and reprovision public CA |
| MQTT unauthorized | Check per-device password, username=device ID and ACL topic |
| Backend rejects | Compare topic scope/profile/point schema with registered Dev 2 config |

Do not mark hardware runtime PASS until both devices complete this checklist. Pre-hardware build and schema tests prove compilation/contract compatibility, not sensor electrical operation, Wi-Fi range, certificate reachability or MC-38 polarity.
