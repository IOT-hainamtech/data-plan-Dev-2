"""nextWireless M1 ingestion with authenticated TLS MQTT transport."""
import json
import logging
import os
import ssl
import threading
import time
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb
import paho.mqtt.client as mqtt

from rule_engine import evaluate_due_rules, on_event, on_telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("data-plane")


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_timestamp(value):
    if value is None:
        return now_utc(), "server"
    if not isinstance(value, str):
        raise ValueError("ts must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ts") from exc
    if parsed.tzinfo is None:
        raise ValueError("ts must include timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None), "device"


def db_connection():
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "nextwireless"),
        user=os.environ.get("DB_USER", "nw_user"),
        password=os.environ["DB_PASSWORD"],
        connect_timeout=5,
        autocommit=False,
    )


def require_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def validate_telemetry_data(data):
    allowed = {"temperature", "humidity", "quality"}
    if not data or not set(data).issubset(allowed):
        raise ValueError("unsupported telemetry metric")
    quality = data.get("quality", "ok")
    if quality not in ("ok", "error"):
        raise ValueError("quality must be ok or error")
    metrics = {key: value for key, value in data.items() if key != "quality"}
    if quality == "error":
        if metrics:
            raise ValueError("error telemetry must not contain measurements")
        return quality, metrics
    if not metrics:
        raise ValueError("ok telemetry requires measurements")
    return quality, metrics


def door_heartbeat_event(profile, body):
    door_state = body["data"].get("door_state")
    if door_state is None:
        return None
    if profile != "DOOR_SENSOR" or door_state not in ("open", "closed"):
        raise ValueError("invalid door heartbeat")
    return (f"heartbeat:{body['boot_id']}:{body['seq']}",
            "DOOR_OPENED" if door_state == "open" else "DOOR_CLOSED")


def validate_message(topic, payload):
    parts = topic.split("/")
    if len(parts) != 6 or parts[:2] != ["nw", "v1"]:
        raise ValueError("invalid topic")
    _, _, org, site, device, channel = parts
    if channel not in ("telemetry", "event", "status"):
        raise ValueError("unsupported channel")
    if not isinstance(payload, dict):
        raise ValueError("payload must be JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    if payload.get("device_id") != device:
        raise ValueError("topic/device_id mismatch")
    if not isinstance(payload.get("boot_id"), str) or not payload["boot_id"]:
        raise ValueError("boot_id required")
    require_int(payload.get("seq"), "seq")
    if not isinstance(payload.get("data"), dict):
        raise ValueError("data must be JSON object")
    stamp, source = parse_timestamp(payload.get("ts"))
    if channel == "event":
        if not isinstance(payload.get("event_id"), str) or not payload["event_id"]:
            raise ValueError("event_id required")
        if not isinstance(payload.get("event_type"), str) or not payload["event_type"]:
            raise ValueError("event_type required")
    if channel == "status" and payload.get("state") not in ("online", "offline"):
        raise ValueError("status state must be online/offline")
    return org, site, device, channel, stamp, source


def save_message(topic, body):
    org, site, device, channel, stamp, source = validate_message(topic, body)
    received = now_utc()
    conn = db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT profile_code FROM devices WHERE device_id=%s AND org_code=%s AND site_code=%s AND enabled=TRUE", (device, org, site))
        record = cur.fetchone()
        if record is None:
            raise ValueError("unknown, disabled, or wrongly routed device")
        profile = record[0]
        inserted = 0
        if channel == "telemetry":
            if profile != "TEMP_HUM_SENSOR":
                raise ValueError("telemetry not supported by device profile")
            quality, metrics = validate_telemetry_data(body["data"])
            cur.execute("""INSERT INTO telemetry_reports
                (device_id,boot_id,seq,quality,measured_at,received_at,payload_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (device_id,boot_id,seq) DO NOTHING""",
                (device, body["boot_id"], body["seq"], quality, stamp, received, Jsonb(body)))
            if not cur.rowcount:
                conn.commit()
                return
            inserted = 1
            for metric, value in metrics.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{metric} must be numeric")
                if not (float('-inf') < value < float('inf')):
                    raise ValueError("non-finite metric")
                if metric == "temperature" and not -20 <= value <= 80:
                    raise ValueError("temperature out of range")
                if metric == "humidity" and not 0 <= value <= 100:
                    raise ValueError("humidity out of range")
                cur.execute("""INSERT INTO telemetry
                    (device_id, boot_id, seq, metric, value_number, measured_at, received_at, ts_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (device_id, metric, measured_at) DO NOTHING""",
                    (device, body["boot_id"], body["seq"], metric, value, stamp, received, source))
                new_count = cur.rowcount
                inserted += new_count
                if new_count:
                    on_telemetry(cur, device_id=device, metric=metric, value=float(value),
                                 measured_at=stamp, received_at=received)
        elif channel == "event":
            if profile != "DOOR_SENSOR":
                raise ValueError("event not supported by device profile")
            expected = {"DOOR_OPENED": "open", "DOOR_CLOSED": "closed"}
            event_type = body["event_type"]
            if event_type not in expected or body["data"].get("door_state") != expected[event_type]:
                raise ValueError("invalid door event")
            cur.execute("""INSERT INTO device_events
                (device_id,event_id,event_type,boot_id,seq,event_at,received_at,payload_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (device_id, event_id) DO NOTHING""",
                (device, body["event_id"], event_type, body["boot_id"], body["seq"], stamp,
                 received, Jsonb(body)))
            inserted = cur.rowcount
            if inserted:
                on_event(cur, device_id=device, event_id=body["event_id"],
                         event_type=event_type, received_at=received, payload=body,
                         observed_at=stamp)
        else:
            state = body["state"]
            # Record state history only on transitions, not on each heartbeat.
            cur.execute("SELECT current_state FROM device_status WHERE device_id=%s", (device,))
            previous = cur.fetchone()
            if previous is None or previous[0] != state:
                cur.execute("""INSERT INTO device_status_history
                    (device_id,state,received_at,boot_id) VALUES (%s,%s,%s,%s)""",
                    (device, state, received, body["boot_id"]))
                inserted = 1
            cur.execute("""INSERT INTO device_status (device_id,current_state,received_at,boot_id)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (device_id) DO UPDATE SET current_state=EXCLUDED.current_state,
                received_at=EXCLUDED.received_at, boot_id=EXCLUDED.boot_id""",
                (device, state, received, body["boot_id"]))
            heartbeat = door_heartbeat_event(profile, body)
            if heartbeat:
                heartbeat_id, heartbeat_type = heartbeat
                on_event(cur, device_id=device, event_id=heartbeat_id,
                         event_type=heartbeat_type, received_at=received, payload=body,
                         observed_at=stamp)
        conn.commit()
        LOG.info("SAVED %s device=%s inserted=%d", channel, device, inserted)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        LOG.error("MQTT connection rejected: %s", reason_code)
        return
    topic = "nw/v1/+/+/+/+"
    client.subscribe(topic, qos=1)
    LOG.info("MQTT connected, listening on %s", topic)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    if reason_code.is_failure:
        LOG.warning("MQTT disconnected unexpectedly (%s); Paho will reconnect", reason_code)
    else:
        LOG.info("MQTT disconnected cleanly")


def configure_mqtt_security(client):
    username = os.environ["MQTT_USERNAME"]
    password = os.environ["MQTT_PASSWORD"]
    ca_file = os.environ["MQTT_CA_FILE"]
    minimum = os.getenv("MQTT_TLS_MIN_VERSION", "1.2")
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_file)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = (ssl.TLSVersion.TLSv1_3 if minimum == "1.3"
                               else ssl.TLSVersion.TLSv1_2)
    client.tls_set_context(context)
    client.username_pw_set(username, password)
    client.reconnect_delay_set(min_delay=1, max_delay=30)


def on_message(client, userdata, message):
    try:
        body = json.loads(message.payload.decode("utf-8"))
        save_message(message.topic, body)
    except (ValueError, KeyError, TypeError, psycopg.Error) as exc:
        LOG.warning("REJECTED topic=%s reason=%s", message.topic, exc)
    except Exception:
        LOG.exception("Unexpected processing failure topic=%s", message.topic)


def rule_timer(stop_event):
    interval = max(1, int(os.getenv("RULE_TIMER_INTERVAL", "5")))
    while not stop_event.wait(interval):
        try:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    changes = evaluate_due_rules(cur, now_utc())
                conn.commit()
            if changes:
                LOG.info("Rule timer persisted %d alarm occurrence(s)", changes)
        except Exception:
            LOG.exception("Rule timer iteration failed")


def main():
    mqtt_host = os.getenv("MQTT_HOST", "mqtt")
    mqtt_port = int(os.getenv("MQTT_PORT", "8883"))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="nw-m1-data-plane-local", clean_session=False)
    configure_mqtt_security(client)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    stop_event = threading.Event()
    timer = threading.Thread(target=rule_timer, args=(stop_event,), name="rule-timer", daemon=True)
    timer.start()
    while True:
        try:
            client.connect(mqtt_host, mqtt_port, keepalive=60)
            client.loop_forever(retry_first_connection=True)
        except (OSError, ssl.SSLError, mqtt.MQTTException) as exc:
            LOG.warning("MQTT unavailable (%s), retrying", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
