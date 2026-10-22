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


def validate_telemetry_data(payload, point_definitions=None):
    data = payload["data"]
    definitions = point_definitions or {
        "temperature": {"data_type": "number", "min_value": -20, "max_value": 80},
        "humidity": {"data_type": "number", "min_value": 0, "max_value": 100},
    }
    allowed = set(definitions)
    quality = payload.get("quality", "good")
    if quality not in ("good", "error"):
        raise ValueError("quality must be good or error")
    if not set(data).issubset(allowed):
        raise ValueError("unsupported telemetry metric")
    metrics = data
    if quality == "error":
        if metrics:
            raise ValueError("error telemetry must not contain measurements")
        return quality, metrics
    if not metrics:
        raise ValueError("good telemetry requires measurements")
    for metric, value in metrics.items():
        definition = definitions[metric]
        if definition["data_type"] == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{metric} must be numeric")
            if not (float('-inf') < value < float('inf')):
                raise ValueError("non-finite metric")
            minimum, maximum = definition.get("min_value"), definition.get("max_value")
            if minimum is not None and value < minimum or maximum is not None and value > maximum:
                raise ValueError(f"{metric} out of range")
    return quality, metrics


def load_profile_contract(cur, profile, channel):
    cur.execute("""SELECT point_code,data_type,min_value,max_value,allowed_values,required
        FROM device_profile_points WHERE profile_code=%s AND channel=%s""", (profile, channel))
    definitions = {}
    for code, data_type, minimum, maximum, allowed, required in cur.fetchall():
        definitions[code] = {"data_type": data_type, "min_value": minimum,
                             "max_value": maximum, "allowed_values": allowed,
                             "required": required}
    return definitions


def door_heartbeat_event(profile, body, event_types=None):
    door_state = body["data"].get("door_state")
    if door_state is None:
        return None
    mapping = event_types or ({"open": "DOOR_OPENED", "closed": "DOOR_CLOSED"}
                              if profile == "DOOR_SENSOR" else {})
    if door_state not in mapping:
        raise ValueError("invalid door heartbeat")
    return (f"heartbeat:{body['boot_id']}:{body['seq']}",
            mapping[door_state])


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


def update_device_presence(cur, *, device_id, boot_id, received_at, explicit_state=None):
    """Update device presence, last_seen_at timestamp, and record transitions without duplicates."""
    cur.execute("SELECT current_state FROM device_status WHERE device_id=%s FOR UPDATE", (device_id,))
    row = cur.fetchone()
    target_state = explicit_state or "online"
    prev_state = row[0] if row else None
    inserted = 0

    if prev_state != target_state:
        cur.execute("""INSERT INTO device_status_history
            (device_id, state, received_at, boot_id) VALUES (%s, %s, %s, %s)""",
            (device_id, target_state, received_at, boot_id))
        inserted = 1

    cur.execute("""INSERT INTO device_status
        (device_id, current_state, received_at, boot_id, last_seen_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (device_id) DO UPDATE SET
            current_state = EXCLUDED.current_state,
            received_at = EXCLUDED.received_at,
            boot_id = EXCLUDED.boot_id,
            last_seen_at = COALESCE(EXCLUDED.last_seen_at, device_status.last_seen_at)""",
        (device_id, target_state, received_at, boot_id, received_at if target_state == "online" else None))
    return inserted


def reconcile_device_timeouts(cur, now, device_id=None):
    """Mark online devices as offline if no message has been received within timeout_seconds."""
    cur.execute("""SELECT device_id, boot_id, last_seen_at, timeout_seconds
        FROM device_status
        WHERE current_state = 'online'
          AND last_seen_at IS NOT NULL
          AND last_seen_at <= %s - (timeout_seconds || ' seconds')::interval
          AND (%s::text IS NULL OR device_id = %s)
        FOR UPDATE""", (now, device_id, device_id))
    timed_out = cur.fetchall()
    for dev_id, boot_id, last_seen, timeout_sec in timed_out:
        cur.execute("""INSERT INTO device_status_history
            (device_id, state, received_at, boot_id)
            VALUES (%s, 'offline', %s, %s)""",
            (dev_id, now, boot_id))
        cur.execute("""UPDATE device_status
            SET current_state = 'offline', received_at = %s
            WHERE device_id = %s""",
            (now, dev_id))
        LOG.warning("DEVICE_OFFLINE_TIMEOUT device=%s last_seen=%s age=%.1fs timeout=%ds",
                    dev_id, last_seen, (now - last_seen).total_seconds(), timeout_sec)
    return len(timed_out)


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
            point_definitions = load_profile_contract(cur, profile, "telemetry")
            if not point_definitions:
                raise ValueError("telemetry not supported by device profile")
            quality, metrics = validate_telemetry_data(body, point_definitions)
            cur.execute("""INSERT INTO telemetry_reports
                (device_id,boot_id,seq,quality,measured_at,received_at,payload_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (device_id,boot_id,seq) DO NOTHING""",
                (device, body["boot_id"], body["seq"], quality, stamp, received, Jsonb(body)))
            if not cur.rowcount:
                conn.commit()
                return
            inserted = 1
            update_device_presence(cur, device_id=device, boot_id=body["boot_id"], received_at=received)
            for metric, value in metrics.items():
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
            event_type = body["event_type"]
            cur.execute("""SELECT state_point,state_value FROM device_profile_events
                WHERE profile_code=%s AND event_type=%s""", (profile, event_type))
            event_contract = cur.fetchone()
            if event_contract is None:
                raise ValueError("event not supported by device profile")
            state_point, state_value = event_contract
            if state_point and body["data"].get(state_point) != state_value:
                raise ValueError("invalid door event")
            cur.execute("""INSERT INTO device_events
                (device_id,event_id,event_type,boot_id,seq,event_at,received_at,payload_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (device_id, event_id) DO NOTHING""",
                (device, body["event_id"], event_type, body["boot_id"], body["seq"], stamp,
                 received, Jsonb(body)))
            inserted = cur.rowcount
            if inserted:
                update_device_presence(cur, device_id=device, boot_id=body["boot_id"], received_at=received)
                on_event(cur, device_id=device, event_id=body["event_id"],
                         event_type=event_type, received_at=received, payload=body,
                         observed_at=stamp)
        else:
            state = body["state"]
            inserted = update_device_presence(cur, device_id=device, boot_id=body["boot_id"],
                                              received_at=received, explicit_state=state)
            points = load_profile_contract(cur, profile, "status")
            cur.execute("""SELECT state_value,event_type FROM device_profile_events
                WHERE profile_code=%s AND state_point='door_state'""", (profile,))
            event_types = dict(cur.fetchall())
            heartbeat = door_heartbeat_event(profile, body, event_types) if "door_state" in points else None
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
            now = now_utc()
            with db_connection() as conn:
                with conn.cursor() as cur:
                    changes = evaluate_due_rules(cur, now)
                    timed_out = reconcile_device_timeouts(cur, now)
                conn.commit()
            if changes:
                LOG.info("Rule timer persisted %d alarm occurrence(s)", changes)
            if timed_out:
                LOG.info("Status tracker marked %d device(s) offline due to timeout", timed_out)
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
