#!/bin/sh
set -eu
umask 077
base=/work
certs="$base/mosquitto/certs"
secrets="$base/.secrets"
lan_ip="${MQTT_SERVER_LAN_IP:-}"
mkdir -p "$certs" "$secrets"
if [ ! -s "$certs/ca.key" ]; then
  openssl genrsa -out "$certs/ca.key" 3072
  openssl req -x509 -new -key "$certs/ca.key" -sha256 -days 825 \
    -subj "/CN=nextWireless Development MQTT CA" -out "$certs/ca.crt"
fi
if [ -n "$lan_ip" ] && ! printf '%s\n' "$lan_ip" | awk -F. '
  NF != 4 { exit 1 }
  { for (i=1; i<=4; i++) if ($i !~ /^[0-9]+$/ || $i > 255) exit 1 }
'; then
  echo "MQTT_SERVER_LAN_IP must be a valid IPv4 address" >&2
  exit 1
fi
cat > "$certs/server.cnf" <<'EOF'
[req]
prompt=no
distinguished_name=dn
req_extensions=req_ext
[dn]
CN=mqtt
[req_ext]
subjectAltName=@alt_names
[alt_names]
DNS.1=mqtt
DNS.2=localhost
IP.1=127.0.0.1
EOF
if [ -n "$lan_ip" ]; then
  printf 'IP.2=%s\n' "$lan_ip" >> "$certs/server.cnf"
fi
openssl genrsa -out "$certs/server.key" 3072
openssl req -new -key "$certs/server.key" -out "$certs/server.csr" -config "$certs/server.cnf"
openssl x509 -req -in "$certs/server.csr" -CA "$certs/ca.crt" -CAkey "$certs/ca.key" \
  -CAcreateserial -out "$certs/server.crt" -days 397 -sha256 \
  -extensions req_ext -extfile "$certs/server.cnf"
rm -f "$certs/server.csr" "$certs/ca.srl"
if [ ! -e "$secrets/mqtt-broker.env" ] && [ ! -e "$secrets/mqtt-data-plane.env" ] && \
   [ ! -e "$secrets/mqtt-simulator.env" ]; then
  data_plane_password="$(openssl rand -base64 32 | tr -d '\r\n')"
  th001_password="$(openssl rand -base64 32 | tr -d '\r\n')"
  dr001_password="$(openssl rand -base64 32 | tr -d '\r\n')"
  th002_password="$(openssl rand -base64 32 | tr -d '\r\n')"
  printf 'MQTT_DATA_PLANE_PASSWORD=%s\nMQTT_TH001_PASSWORD=%s\nMQTT_DR001_PASSWORD=%s\nMQTT_TH002_PASSWORD=%s\n' \
    "$data_plane_password" "$th001_password" "$dr001_password" "$th002_password" > "$secrets/mqtt-broker.env"
  printf 'MQTT_USERNAME=data-plane\nMQTT_PASSWORD=%s\n' \
    "$data_plane_password" > "$secrets/mqtt-data-plane.env"
  printf 'TH001_MQTT_USERNAME=TH-001\nTH001_MQTT_PASSWORD=%s\nDR001_MQTT_USERNAME=DR-001\nDR001_MQTT_PASSWORD=%s\nTH002_MQTT_USERNAME=TH-002\nTH002_MQTT_PASSWORD=%s\n' \
    "$th001_password" "$dr001_password" "$th002_password" > "$secrets/mqtt-simulator.env"
elif [ ! -s "$secrets/mqtt-broker.env" ] || [ ! -s "$secrets/mqtt-data-plane.env" ] || \
     [ ! -s "$secrets/mqtt-simulator.env" ]; then
  echo "Refusing to replace a partial MQTT secret set; remove all three files intentionally." >&2
  exit 1
fi
if [ -s "$secrets/mqtt-broker.env" ] && ! grep -q "MQTT_TH002_PASSWORD=" "$secrets/mqtt-broker.env"; then
  th002_password="$(openssl rand -base64 32 | tr -d '\r\n')"
  printf 'MQTT_TH002_PASSWORD=%s\n' "$th002_password" >> "$secrets/mqtt-broker.env"
  printf 'TH002_MQTT_USERNAME=TH-002\nTH002_MQTT_PASSWORD=%s\n' "$th002_password" >> "$secrets/mqtt-simulator.env"
fi
chmod 600 "$secrets"/*.env "$certs"/*.key
echo "Generated development MQTT server certificate; preserved existing CA and credentials when present."
