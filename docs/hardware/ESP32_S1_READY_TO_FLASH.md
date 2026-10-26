# ESP32 S1 (TH-001) Ready to Flash

Target: ESP32 DevKit V1 / ESP32-WROOM-32, 30-pin, PlatformIO environment `sht31`. Firmware build was verified on 2026-10-26 with ESP-IDF 5.5.0. Sensor runtime remains unverified until real hardware is connected.

## Wiring

Power off USB before changing wiring.

| SHT31-D | ESP32 | Notes |
|---|---|---|
| VIN/VCC | 3V3 | Never use 5 V unless the exact breakout explicitly supports it |
| GND | GND | Common ground |
| SDA | GPIO21 | Firmware fixed I²C SDA |
| SCL | GPIO22 | Firmware fixed I²C SCL |
| ADDR | GND or board default | Address `0x44` (NVS decimal 68) |
| ADDR | 3V3 | Address `0x45` (NVS decimal 69), if required |

The firmware probes the configured address with an SHT31 soft reset, retries reads three times, validates both CRC bytes, applies offsets, and accepts only the active backend range −20..80 °C and 0..100 %RH. An invalid reading publishes telemetry with `quality=error` and empty `data`; it never fabricates zero values.

## Configure and flash

From repository root, generate ignored NVS data using interactive secret prompts:

```powershell
.\scripts\new-esp32-nvs.ps1 -DeviceId TH-001
```

Use `-UseExistingMqttSecret` only when the local ignored broker secret already exists. Confirm the template broker IP and certificate SAN first. To use address `0x45`, change `sht31_address` to decimal `69` in the protected local CSV and regenerate its 0x6000-byte NVS image.

Identify the port by listing, connecting only this board, and listing again:

```powershell
.\firmware\.venv\Scripts\pio.exe device list
```

Build, upload application, provision NVS, then monitor (replace `COMx`):

```powershell
.\firmware\.venv\Scripts\pio.exe run -d firmware -e sht31
.\firmware\.venv\Scripts\pio.exe run -d firmware -e sht31 -t upload --upload-port COMx
.\firmware\.venv\Scripts\python.exe "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" `
  --chip esp32 --port COMx write_flash 0x9000 firmware\config\nvs.TH-001.bin
.\firmware\.venv\Scripts\pio.exe device monitor -d firmware -p COMx -b 115200
```

If auto-reset fails, hold BOOT while upload starts and release after `Connecting...`.

## Expected behavior

Expected serial milestones include `Starting device=TH-001 sensor=sht31`, `SHT31 ready at 0x44 on SDA=21/SCL=22`, Wi-Fi/IP and MQTT framework connection messages. Every 60 seconds the device publishes QoS 1 to `nw/v1/demo/demo-site/TH-001/telemetry`:

```json
{"schema_version":1,"device_id":"TH-001","boot_id":"...","seq":2,"ts":"2026-10-26T08:00:00Z","quality":"good","data":{"temperature":28.4,"humidity":56.2}}
```

Birth/heartbeat/LWT use `nw/v1/demo/demo-site/TH-001/status`. Confirm ingestion with `docker compose logs -f data-plane` and look for `SAVED telemetry device=TH-001`.

## Troubleshooting and remaining hardware checks

- `Configuration rejected`: NVS missing/invalid; regenerate and flash at `0x9000`.
- Sensor not responding: check 3V3/GND, SDA/SCL, configured 0x44 versus 0x45, and pull-ups on the breakout.
- Repeated `quality=error`: check wiring/CRC noise and offsets; do not treat this as a valid measurement.
- TLS verification failure: broker IP/hostname must appear in server certificate SAN and device must contain the matching CA.
- No Wi-Fi: ESP32 supports 2.4 GHz only; check SSID security and signal.

Still requires real hardware: USB driver/COM behavior, I²C electrical integrity, actual SHT31 address, temperature/humidity plausibility and calibration.
