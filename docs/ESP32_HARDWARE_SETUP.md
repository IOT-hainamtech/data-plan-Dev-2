# ESP32 hardware setup

Physical verification is **PENDING HARDWARE**. This document is a wiring and
bring-up procedure, not evidence that the components have been tested.

## Parts checklist

- 2 × ESP32 DevKit V1
- 1 × SHT31 module
- 1 × MC-38 magnetic reed contact
- breadboard and jumper wires
- 2 × known data-capable USB cables
- suitable USB power supply
- Windows host running Docker Desktop
- 2.4 GHz Wi-Fi reachable by both ESP32 boards

An additional TH-002 may be simulated for multi-device load scenarios; it is not
a substitute for physical TH-001 verification.

## TH-001: ESP32 + SHT31

| ESP32 | SHT31 |
|---|---|
| 3V3 | VCC, only after confirming the module supports 3.3 V |
| GND | GND |
| GPIO21 | SDA |
| GPIO22 | SCL |

Before power-up, check module voltage requirements and wiring continuity. Scan or
verify address 0x44 (or configured 0x45), and check whether the breakout already
has I2C pull-ups. Firmware validates both SHT31 CRC bytes. Compare readings against
a reference instrument before setting temperature/humidity offsets. Disconnecting
the sensor must produce top-level `quality="error"` with empty `data`, never a fake
zero reading.

## DR-001: ESP32 + MC-38

Connect one MC-38 lead to GPIO27 and the other to GND. Firmware enables
`INPUT_PULLUP`; do not apply an external voltage to the contact. Observe the raw
level with the magnet present/absent and configure `door_closed_level` to the level
that physically means closed. Confirm 50 ms debounce, no synthetic transition at
boot, one event after each stable transition, and a 60-second status heartbeat.

## Safe bring-up order

1. Verify wiring unpowered and identify each board's COM port.
2. Provision and flash one board at a time.
3. Watch serial output for configuration, Wi-Fi, NTP, TLS, and MQTT status; secrets
   must never appear.
4. Confirm retained online state, then remove power and confirm LWT offline.
5. For TH-001, test normal readings, CRC/error behavior, and offsets.
6. For DR-001, establish closed-level polarity, debounce, event, and heartbeat.
7. Query PostgreSQL/Data API and verify S1/S6/outbox behavior.
