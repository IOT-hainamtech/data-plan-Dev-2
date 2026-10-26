# ESP32 provisioning (TH-001 and DR-001)

No Wi-Fi or MQTT secret is stored in source control. Run provisioning locally and
enter secrets at the prompt; do not paste them into tickets, chat, or serial logs.

## Prerequisites

1. Confirm Wi-Fi is 2.4 GHz and the broker host is still `192.168.1.40`.
2. Confirm `mosquitto/certs/server.crt` has SAN `IP:192.168.1.40`.
3. Confirm the broker accounts remain `TH-001` and `DR-001` with their existing ACLs.
4. Build firmware and keep `mosquitto/certs/ca.key` and `server.key` off devices.

The committed templates are `firmware/config/nvs.th001.template.csv` and
`firmware/config/nvs.dr001.template.csv`. Generated CSV/images and copied CA files
are ignored by Git.

## Generate a device NVS image

Use the MQTT passwords already provisioned to the development broker without
printing them:

```powershell
.\scripts\new-esp32-nvs.ps1 -DeviceId TH-001 -UseExistingMqttSecret
.\scripts\new-esp32-nvs.ps1 -DeviceId DR-001 -UseExistingMqttSecret
```

If the broker address changes, pass it explicitly instead of editing committed templates:

```powershell
.\scripts\new-esp32-nvs.ps1 -DeviceId TH-001 -MqttHost 192.168.1.40
.\scripts\new-esp32-nvs.ps1 -DeviceId DR-001 -MqttHost 192.168.1.40
```

The script prompts locally for Wi-Fi SSID/password, embeds only the public CA,
generates a 0x6000-byte partition image, refuses overwrite by default, and backs
up an existing image/CSV when `-Force` is explicitly supplied. If the existing
broker secret files are unavailable, omit `-UseExistingMqttSecret` and enter the
per-device MQTT password through the secure prompt.

Until Wi-Fi credentials are entered locally, real provisioning is
**BLOCKED: CREDENTIALS REQUIRED**.

## Build and flash after identifying the correct serial port

Never guess the COM port. Disconnect one board, list ports, reconnect it, and
identify the new port:

```powershell
.\firmware\.venv\Scripts\pio.exe device list
```

TH-001:

```powershell
cd firmware
.\.venv\Scripts\pio.exe run -e sht31
.\.venv\Scripts\pio.exe run -e sht31 -t upload --upload-port COMx
.\.venv\Scripts\python.exe "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" `
  --chip esp32 --port COMx write_flash 0x9000 config\nvs.TH-001.bin
.\.venv\Scripts\pio.exe device monitor -p COMx -b 115200
```

DR-001 uses `-e reed_door` and `config\nvs.DR-001.bin`.

## Recovery

- Save the generated `.local.csv` and `.bin` in a protected local backup before
  replacing NVS. The provisioning script also creates timestamped backups with
  `-Force`.
- For a bad configuration, correct the local CSV by rerunning the provisioning
  script and flash only offset `0x9000`; the application partition is unaffected.
- If credentials may have leaked, rotate only that device account with
  `manage-mqtt-account.ps1`, regenerate its NVS, and reconnect it.
- Do not use whole-chip erase unless data-loss implications are understood.

## Configuration mapping

| Setting | TH-001 | DR-001 |
|---|---|---|
| Profile | `TEMP_HUM_SENSOR` | `DOOR_SENSOR` |
| Sensor | `sht31` | `reed_door` |
| Broker | `mqtts://192.168.1.40:8883` | same |
| Org/site | `demo` / `demo-site` | same |
| Period | telemetry 60 s | heartbeat 60 s |
| Pins | SDA 21, SCL 22 | GPIO27, `INPUT_PULLUP` |
| Sensor options | address 0x44/0x45; T/RH offsets | debounce 50 ms; closed level 0/1 |

The `sht31` and `reed_door` builds embed different expected sensor roles. Firmware rejects an NVS image whose `sensor_type` belongs to the other environment; this prevents accidentally operating TH-001 as a door node or DR-001 as an SHT31 node.

The firmware waits for NTP before TLS, verifies the CA and SAN, uses MQTT 3.1.1,
QoS 1/PUBACK tracking, retained birth/LWT, three in-flight messages, stable retry
payload/sequence numbers, and bounded exponential MQTT/Wi-Fi reconnect.
