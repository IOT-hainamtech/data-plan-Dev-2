# MQTT TLS over the local IoT LAN

This is a development-only setup. It does not expose MQTT 1883, enable anonymous
access, disable certificate verification, or configure router/Internet forwarding.

## Confirmed host network (2026-10-17)

- Windows adapter: `Wi-Fi` (`hai nam tech`, Private profile)
- Host IPv4: `192.168.1.87/24`
- Gateway: `192.168.1.1`
- Intended broker URL: `mqtts://192.168.1.87:8883`
- Intended firewall scope: TCP 8883 from `192.168.1.0/24` to local
  `192.168.1.87`, Private profile only

Re-check these values after changing Wi-Fi or DHCP lease. Do not reuse the commands
below if the address or subnet has changed.

## Prepare certificate and preview firewall

Run from an ordinary PowerShell prompt in the repository root:

```powershell
.\scripts\generate-dev-mqtt-assets.ps1 -LanIp 192.168.1.87
.\scripts\configure-mqtt-lan-firewall.ps1 `
  -LocalIp 192.168.1.87 -RemoteSubnet 192.168.1.0/24
```

The first command preserves the existing development CA and MQTT password files,
but issues a new ignored server key/certificate with `IP:192.168.1.87` in its SAN.
The second command is preview-only without `-Apply`.

## Enable after explicit approval

Open an elevated PowerShell prompt, apply the restricted firewall rule, then
recreate only Mosquitto so PostgreSQL and its volume are untouched:

```powershell
.\scripts\configure-mqtt-lan-firewall.ps1 `
  -LocalIp 192.168.1.87 -RemoteSubnet 192.168.1.0/24 -Apply
$env:MQTT_BIND_IP = '192.168.1.87'
docker compose up -d --no-deps --force-recreate mqtt
```

Keep `MQTT_BIND_IP` set for later Compose operations that recreate the broker.
Without it, the safe default is restored to `127.0.0.1`.

Verify the listener and run the complete LAN-address test:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8883
$env:MQTT_BIND_IP = '192.168.1.87'
.\scripts\test-mqtt-lan.ps1 -BrokerIp 192.168.1.87
```

The suite checks the trusted CA, rejection of an untrusted CA and wrong password,
both device ACL directions, MQTT 3.1.1 QoS 1/PUBACK, retained birth/LWT, and a
unique telemetry report reaching PostgreSQL exactly once.

## Test from another LAN machine

Copy only `mosquitto/certs/ca.crt` to the client machine. Never copy `ca.key`,
`server.key`, `.env`, or `.secrets`. Configure the client with:

- host `192.168.1.87`, port `8883`;
- MQTT 3.1.1 and QoS 1;
- the copied CA with hostname/IP verification enabled;
- the individual TH-001 or DR-001 username/password provisioned out of band.

For a machine with Mosquitto clients, publish a retained birth message using the
device's own topic (enter the real password only on that trusted client):

```powershell
mosquitto_pub -h 192.168.1.87 -p 8883 --cafile .\ca.crt `
  -V mqttv311 -q 1 -r -u TH-001 -P '<device-password>' `
  -t nw/v1/demo/demo-site/TH-001/status `
  -m '{"schema_version":1,"device_id":"TH-001","boot_id":"external-test","seq":1,"ts":"2026-10-17T00:00:00Z","state":"online","data":{}}'
```

Use the ESP32 or a second client process to validate abrupt-disconnect LWT. Confirm
the resulting state/telemetry on the Windows host with `docker compose exec -T
data-plane python check.py`. Until this is run from a genuinely separate host,
record the result as **PENDING EXTERNAL LAN TEST**.

## Roll back LAN exposure

```powershell
$env:MQTT_BIND_IP = '127.0.0.1'
docker compose up -d --no-deps --force-recreate mqtt
.\scripts\configure-mqtt-lan-firewall.ps1 `
  -LocalIp 192.168.1.87 -RemoteSubnet 192.168.1.0/24 -Remove
```
