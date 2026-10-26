# ESP32 functional test checklist

No authoritative URD v1.1 file is present in the workspace. The FT-01–FT-12
checklist below is derived from the acceptance criteria supplied for this project;
the numbering must be reconciled with the controlled URD before formal acceptance.

| Test | Procedure / expected result | Status before hardware |
|---|---|---|
| FT-01 | TH-001 boots, joins 2.4 GHz Wi-Fi, synchronizes NTP, then establishes verified TLS | PENDING HARDWARE |
| FT-02 | DR-001 performs the same ordered Wi-Fi/NTP/TLS startup | PENDING HARDWARE |
| FT-03 | Valid per-device credentials work; wrong password/CA and cross-device publish fail | PREPARED; physical client pending |
| FT-04 | TH-001 sends QoS 1 telemetry every 60 s with stable boot_id/monotonic seq | PENDING HARDWARE |
| FT-05 | SHT31 CRC/read failure sends `quality=error`, `data={}` and does not evaluate S1 | PENDING HARDWARE |
| FT-06 | S1 triggers/clears with configured holds, hysteresis, quiet window and no duplicate occurrence | PREPARED; sensor stimulus pending |
| FT-07 | DR-001 boot creates no false event; stable transitions emit unique opened/closed event IDs | PENDING HARDWARE |
| FT-08 | DR-001 heartbeat reconciles a deliberately missed event and S6 timing/business hours remain correct | PREPARED; hardware loss test pending |
| FT-09 | QoS 1 PUBACK caps in-flight messages at three and retry reuses payload/seq/event_id | PENDING HARDWARE |
| FT-10 | Retained birth is online; abrupt power/network loss produces retained LWT offline; reconnect recovers | PENDING HARDWARE |
| FT-11 | MQTT ingestion is idempotent and PostgreSQL, Rule Engine, alarm/outbox, API remain healthy | PREPARED; end-to-end hardware pending |
| FT-12 | Restart broker/Data Plane/device; durable state and deduplication survive without false alarms | PENDING HARDWARE |

Record timestamp, firmware commit, device MAC/serial, test equipment, observed MQTT
payloads, database rows, and pass/fail evidence for every physical execution.
