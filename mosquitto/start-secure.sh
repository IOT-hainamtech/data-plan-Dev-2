#!/bin/sh
set -eu
security_dir=/mosquitto/data/security
password_file="$security_dir/passwords"
mkdir -p "$security_dir"
mkdir -p "$security_dir/certs"
cp /mosquitto/certs-src/ca.crt /mosquitto/certs-src/server.crt \
   /mosquitto/certs-src/server.key "$security_dir/certs/"
chown -R mosquitto:mosquitto "$security_dir/certs"
chmod 644 "$security_dir/certs/ca.crt" "$security_dir/certs/server.crt"
chmod 600 "$security_dir/certs/server.key"
if [ ! -s "$password_file" ]; then
    : "${MQTT_DATA_PLANE_PASSWORD:?missing MQTT_DATA_PLANE_PASSWORD}"
    : "${MQTT_TH001_PASSWORD:?missing MQTT_TH001_PASSWORD}"
    : "${MQTT_DR001_PASSWORD:?missing MQTT_DR001_PASSWORD}"
    mosquitto_passwd -b -c "$password_file" data-plane "$MQTT_DATA_PLANE_PASSWORD"
    mosquitto_passwd -b "$password_file" TH-001 "$MQTT_TH001_PASSWORD"
    mosquitto_passwd -b "$password_file" DR-001 "$MQTT_DR001_PASSWORD"
fi
if [ -n "${MQTT_TH002_PASSWORD:-}" ]; then
    mosquitto_passwd -b "$password_file" TH-002 "$MQTT_TH002_PASSWORD"
fi
# Mosquitto 2 warns that a non-root-owned password file will be rejected by a future release.
# The broker runs in group mosquitto, so root ownership with group-read is sufficient.
chown root:mosquitto "$password_file"
chmod 640 "$password_file"
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
