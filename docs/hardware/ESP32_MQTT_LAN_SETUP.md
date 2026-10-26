# ESP32 MQTT TLS LAN Setup

ESP32 cannot reach broker mapping `127.0.0.1:8883`; loopback means the ESP32 itself. The safe repository default deliberately binds Mosquitto only to Windows loopback. For hardware testing, bind it to the current private-LAN IPv4 and permit TCP 8883 only from the local IoT subnet. Never configure router port-forwarding or a public firewall rule.

## Preconditions and topology

| Item | Required value |
|---|---|
| Transport | MQTT 3.1.1 over TLS, port 8883 |
| Authentication | Per-device username/password; username equals device ID |
| Broker anonymous access | Disabled |
| S1 ACL | write only `nw/v1/demo/demo-site/TH-001/#` |
| S6 ACL | write only `nw/v1/demo/demo-site/DR-001/#` |
| Ingestor | read `nw/v1/#` inside Docker network |
| Internet exposure | None |

The committed templates currently use `mqtts://192.168.1.40:8883`. That address is not permanent. `ipconfig` verified the active Wi-Fi address as `192.168.1.40/24` on 2026-10-26; reserve it in DHCP if possible and re-check before provisioning.

Current preparation status (2026-10-26): server certificate SAN includes `192.168.1.40`; Mosquitto is healthy and bound to `192.168.1.40:8883`; TLS/ACL/ingestion LAN tests pass. The existing Windows firewall rule still targets old local address `192.168.1.87` because the current session is not elevated. Before connecting ESP32, run the `-Apply` command below in an Administrator PowerShell; the script refuses mismatched existing rules, so remove the old scoped rule first and recreate it explicitly:

```powershell
.\scripts\configure-mqtt-lan-firewall.ps1 `
  -LocalIp 192.168.1.87 -RemoteSubnet 192.168.1.0/24 -Remove
.\scripts\configure-mqtt-lan-firewall.ps1 `
  -LocalIp 192.168.1.40 -RemoteSubnet 192.168.1.0/24 -Apply
```

Hardware pin reference (network setup does not add any Ethernet wiring):

| Node | Peripheral pin | ESP32 pin |
|---|---|---|
| TH-001 | SHT31 SDA / SCL / VCC / GND | GPIO21 / GPIO22 / 3V3 / GND |
| DR-001 | MC-38 two dry-contact leads | GPIO27 / GND |

## Generate TLS assets and restrict Windows firewall

For the verified host `192.168.1.40/24`:

```powershell
.\scripts\generate-dev-mqtt-assets.ps1 -LanIp 192.168.1.40
.\scripts\configure-mqtt-lan-firewall.ps1 `
  -LocalIp 192.168.1.40 -RemoteSubnet 192.168.1.0/24
```

The firewall command above is preview-only. Verify that the Wi-Fi profile is Private, certificate generation completed, and the server certificate SAN contains `IP Address:192.168.1.40`. One way to inspect it is:

```powershell
docker run --rm -v "${PWD}/mosquitto/certs:/certs:ro" alpine:3.22 `
  sh -c "apk add --no-cache openssl >/dev/null && openssl x509 -in /certs/server.crt -noout -ext subjectAltName"
```

Then use an elevated PowerShell only for the scoped firewall change:

```powershell
.\scripts\configure-mqtt-lan-firewall.ps1 `
  -LocalIp 192.168.1.40 -RemoteSubnet 192.168.1.0/24 -Apply
$env:MQTT_BIND_IP = '192.168.1.40'
docker compose up -d --no-deps --force-recreate mqtt
Get-NetTCPConnection -State Listen -LocalPort 8883
```

Update the ignored/generated device NVS inputs if the broker address changed, regenerate both NVS images, and copy only the public CA to device provisioning. Never copy `ca.key`, `server.key`, `.env`, `.secrets`, MQTT passwords or Wi-Fi passwords into Git.

## Verification

```powershell
$env:MQTT_BIND_IP = '192.168.1.40'
.\scripts\test-mqtt-lan.ps1 -BrokerIp 192.168.1.40
docker compose logs -f mqtt data-plane
```

The LAN suite verifies trusted/untrusted CA behavior, wrong passwords, ACL direction, QoS 1, retained birth/LWT and ingestion. Also test from a second LAN device or the ESP32; a same-host Docker test alone does not prove external reachability.

After provisioning, the exact build/upload/monitor pattern is:

```powershell
.\firmware\.venv\Scripts\pio.exe run -d firmware -e sht31
.\firmware\.venv\Scripts\pio.exe run -d firmware -e sht31 -t upload --upload-port COMx
.\firmware\.venv\Scripts\pio.exe device monitor -d firmware -p COMx -b 115200
# Use -e reed_door for DR-001.
```

Expected device-side result is an MQTT framework `CONNECTED` event followed by an online birth; expected Backend log is `SAVED telemetry device=TH-001`, `SAVED event device=DR-001`, or status processing. Exact framework log wording can vary with ESP-IDF log level.

Common failures: certificate SAN mismatch after DHCP address change, Windows network profile Public, firewall subnet mismatch, client isolation on guest Wi-Fi, 5-GHz-only SSID, or NVS still pointing to the old IP.

Cannot be verified before hardware: ESP32 RF reachability, USB upload/monitor, the router's client-isolation behavior as seen by the ESP32, and end-to-end TLS hostname/IP validation on the physical board.

Rollback LAN exposure:

```powershell
$env:MQTT_BIND_IP = '127.0.0.1'
docker compose up -d --no-deps --force-recreate mqtt
.\scripts\configure-mqtt-lan-firewall.ps1 `
  -LocalIp 192.168.1.40 -RemoteSubnet 192.168.1.0/24 -Remove
```
