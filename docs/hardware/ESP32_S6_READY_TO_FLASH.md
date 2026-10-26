# ESP32 S6 (DR-001) Ready to Flash

Target: ESP32 DevKit V1 / ESP32-WROOM-32, 30-pin, PlatformIO environment `reed_door`. Firmware build was verified on 2026-10-26. Contact polarity and runtime behavior must be confirmed with the real MC-38/MC-38A.

## Wiring

MC-38 is a passive two-wire dry contact. Do not connect 3.3 V to it.

| MC-38 lead | ESP32 | Notes |
|---|---|---|
| Lead 1 | GPIO27 | Configured `INPUT_PULLUP` |
| Lead 2 | GND | Contact pulls GPIO27 low |

With the usual normally-closed arrangement, magnet present/door closed reads LOW and NVS `door_closed_lvl=0`. If the actual contact behaves oppositely, set `door_closed_lvl=1`; do not change source code. `door_debounce` defaults to 50 ms and accepts 10..2000 ms.

## Configure and flash

```powershell
.\scripts\new-esp32-nvs.ps1 -DeviceId DR-001
.\firmware\.venv\Scripts\pio.exe device list
.\firmware\.venv\Scripts\pio.exe run -d firmware -e reed_door
.\firmware\.venv\Scripts\pio.exe run -d firmware -e reed_door -t upload --upload-port COMx
.\firmware\.venv\Scripts\python.exe "$env:USERPROFILE\.platformio\packages\tool-esptoolpy\esptool.py" `
  --chip esp32 --port COMx write_flash 0x9000 firmware\config\nvs.DR-001.bin
.\firmware\.venv\Scripts\pio.exe device monitor -d firmware -p COMx -b 115200
```

Identify `COMx` by disconnect/reconnect comparison; never guess when both boards are connected.

## Expected behavior

Boot log includes `Starting device=DR-001 sensor=reed_door` and `MC-38 ready on GPIO27 INPUT_PULLUP ... initial=open|closed`. A stable transition after debounce logs `Door transition=OPEN|CLOSED` and publishes exactly one QoS 1 event:

```json
{"schema_version":1,"device_id":"DR-001","boot_id":"...","seq":2,"ts":"2026-10-26T08:00:00Z","event_id":"DR-001-...-2","event_type":"DOOR_OPENED","data":{"door_state":"open"}}
```

Close generates `DOOR_CLOSED` with `door_state=closed`. Heartbeats remain on the existing `status` channel with `data.door_state`; the firmware does not change the unresolved Dev 1/Dev 2 profile-channel agreement. Only debounced changes generate event-channel messages. A pending event is preserved while MQTT is unavailable, and polling resumes afterward so the next stable transition is not silently consumed.

Verify Backend with `docker compose logs -f data-plane`; expect `SAVED event device=DR-001` and periodic status processing.

## Troubleshooting and remaining hardware checks

- State inverted: regenerate NVS with `door_closed_lvl=1` after confirming continuity with a multimeter.
- Repeated events: inspect loose wiring and increase debounce; valid range is 10..2000 ms.
- No transition: verify lead-to-GND wiring and that GPIO27 changes electrically; do not power the contact.
- Wrong topic/ACL denial: username/device ID must both be `DR-001` and scope must be `demo/demo-site` unless Dev 1 registry is changed consistently.
- TLS/Wi-Fi faults: follow the LAN document and first-boot checklist.

Still requires real hardware: actual contact type/polarity, magnet gap/alignment, bounce duration, USB upload, Wi-Fi RF and end-to-end event receipt.
