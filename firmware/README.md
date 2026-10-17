# nextWireless M1 ESP32 firmware skeleton

One ESP-IDF/PlatformIO codebase supports `sht31` (S1/TH-001) and `reed_door`
(S6/DR-001). No URD v1.1 document is present in this repository; this firmware
implements the acceptance criteria supplied with the task and must be checked
against the authoritative URD before production use.

## Architecture

- `ConfigManager`: reads and validates configuration from encrypted-capable ESP32
  NVS. Firmware fails closed when required values are missing or MQTT is not
  `mqtts://`.
- `WifiManager`: station mode with event-driven reconnect.
- `TimeSync`: blocks MQTT/TLS startup until NTP has established credible UTC.
- `MqttManager`: ESP-MQTT 3.1.1, server CA/hostname verification, QoS 1 PUBACK
  tracking, retained birth, LWT, three-message application inflight limit and
  reconnect backoff from 1 to 30 seconds.
- `SequenceManager`: random per-boot `boot_id`, monotonic `seq`, stable event ID.
- `Sht31Driver`: GPIO21/GPIO22 I2C, configurable 0x44/0x45 address, CRC-8 and
  calibration offsets.
- `ReedDoorDriver`: GPIO27 `INPUT_PULLUP`, configurable closed level and debounce.
- `main.cpp`: telemetry/event/heartbeat scheduler and task watchdog.

The ESP-MQTT outbox retransmits unacknowledged QoS-1 packets after reconnect.
Application payloads waiting for a free inflight slot retain their original
payload, `seq` and `event_id`; retry does not allocate a new sequence number.

## Message contract

Topics are `nw/v1/{org_code}/{site_code}/{device_id}/{channel}`. The configured
username must equal `device_id`, matching the current Mosquitto ACL model.

- SHT31 success: `telemetry` with numeric `temperature` and `humidity` only.
- SHT31 failure: no fabricated telemetry; a compatible `status` heartbeat carries
  `data.quality="error"`.
- Door transition: `event`, `DOOR_OPENED`/`DOOR_CLOSED`, stable unique `event_id`.
- Door heartbeat: `status` with `state="online"` and `data.door_state`.
- Birth: retained QoS-1 `status=online`; LWT: retained QoS-1 `status=offline`.

The status-channel choice for sensor quality is necessary because the current
Data Plane rejects non-numeric keys in S1 telemetry. Confirm this mapping against
URD v1.1 when that document is available.

## Local configuration and build

Requirements: PlatformIO Core and Python; the optional PlatformIO native C++
test also requires a host GCC/G++ toolchain. From the repository root:

```powershell
Copy-Item mosquitto\certs\ca.crt firmware\certs\mqtt_ca.pem
Copy-Item firmware\config\nvs.example.csv firmware\config\nvs.csv
notepad firmware\config\nvs.csv

cd firmware
pio run -e sht31
pio run -e reed_door
pio test -e native
python -m unittest discover -s tests -v
```

`mqtt_ca.pem`, `nvs.csv`, generated `nvs.bin`, Wi-Fi password and MQTT password
are ignored by Git. Never put a private key on the device.

Generate and flash NVS using Espressif's NVS generator (install
`esp-idf-nvs-partition-gen` in your Python environment if it is not already
available):

```text
python -m esp_idf_nvs_partition_gen generate config/nvs.csv config/nvs.bin 0x6000
esptool.py --chip esp32 write_flash 0x9000 config/nvs.bin
```

Then upload the selected firmware environment. Use separate `nvs.csv` content and
MQTT credentials for TH-001 and DR-001.

## Hardware mapping

| Device | Wiring |
| --- | --- |
| SHT31 | SDA GPIO21, SCL GPIO22, 3.3V, GND; address 0x44 by default |
| MC-38 | signal GPIO27, GND; internal pull-up enabled |

The MC-38 stable state is sampled at startup without producing a synthetic event.
Only a later debounced transition creates an event.

## Before real-device deployment

- Obtain the final Wi-Fi SSID and per-device broker credentials.
- Use a broker hostname present in its certificate SAN; do not disable verification.
- Provision the approved CA and NVS over a controlled manufacturing path.
- Confirm contact polarity, cable length/noise and SHT31 address on hardware.
- Validate NTP reachability, brownout behavior, watchdog recovery and long-duration
  reconnect behavior.
- Enable ESP32 NVS encryption, flash encryption and Secure Boot for production.
- Confirm firmware OTA signing/rollback and credential rotation requirements from
  the authoritative URD/security design.
