# MQTT CA certificate

Place the PEM CA certificate that signed the MQTT broker certificate at
`firmware/certs/mqtt_ca.pem`. That file is intentionally ignored by Git.

For the local Compose broker from this repository:

```powershell
Copy-Item ..\mosquitto\certs\ca.crt .\certs\mqtt_ca.pem
```

Never place a private key in the firmware tree. Production devices must use the
approved production CA, not the development CA.
