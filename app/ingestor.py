"""nextWireless M1 ingestion with authenticated TLS MQTT transport."""
import json
import logging
import os
import ssl
import threading
import time
from collections import defaultdict

import psycopg
from psycopg.types.json import Jsonb
import paho.mqtt as paho_mqtt
import paho.mqtt.client as mqtt

from core.clock import now_utc
from core.database import db_connection
from ingestion.contracts import (door_heartbeat_event, parse_timestamp,
                                 require_int, validate_door_heartbeat, validate_message,
                                 validate_telemetry_data)
from registry import load_device_profile, load_profile_contract, load_profile_event_types
from rules.engine import evaluate_due_rules, on_event, on_telemetry
from status import reconcile_device_timeouts, update_device_presence

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("data-plane")
MAX_MQTT_PAYLOAD_BYTES = int(os.getenv("MQTT_MAX_PAYLOAD_BYTES", "512"))


class DeviceRateLimiter:
    """Token bucket: burst five messages, sustained refill one message/second."""
    def __init__(self):
        self._state = defaultdict(lambda: [5.0, time.monotonic()])
        self._lock = threading.Lock()

    def allow(self, device_id):
        now = time.monotonic()
        with self._lock:
            state = self._state[device_id]
            state[0] = min(5.0, state[0] + now - state[1]); state[1] = now
            if state[0] < 1.0:
                return False
            state[0] -= 1.0
            return True


RATE_LIMITER = DeviceRateLimiter()


def record_ingestion_metric(org, name, *, error_code="", latency_ms=0.0):
    """Best-effort durable counters; metric failure must never stop ingestion."""
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO ingestion_metrics
                    (org_code,metric_name,error_code,metric_value,latency_sum_ms,updated_at)
                    VALUES(%s,%s,%s,1,%s,%s)
                    ON CONFLICT(org_code,metric_name,error_code) DO UPDATE SET
                    metric_value=ingestion_metrics.metric_value+1,
                    latency_sum_ms=ingestion_metrics.latency_sum_ms+EXCLUDED.latency_sum_ms,
                    updated_at=EXCLUDED.updated_at""",
                    (org or "unknown", name, error_code, latency_ms, now_utc()))
            conn.commit()
    except Exception:
        LOG.exception("Unable to update ingestion metric %s", name)


def save_message(topic, body):
    org, site, device, channel, stamp, source = validate_message(topic, body)
    received = now_utc()
    ts_suspect = body.get("ts") is None or abs((received - stamp).total_seconds()) > 300
    conn = db_connection()
    try:
        cur = conn.cursor()
        profile = load_device_profile(cur, device, org, site)
        if profile is None:
            raise ValueError("unknown, disabled, or wrongly routed device")
        inserted = 0
        if channel == "telemetry":
            point_definitions = load_profile_contract(cur, profile, "telemetry")
            if not point_definitions:
                raise ValueError("telemetry not supported by device profile")
            if body.get("msg_kind") == "heartbeat":
                door_state = validate_door_heartbeat(body, point_definitions)
                cur.execute("""INSERT INTO device_state_snapshots
                    (device_id,boot_id,seq,measured_at,received_at,ts_source,ts_suspect,state_json,payload_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (device_id,boot_id,seq) DO NOTHING""",
                    (device,body["boot_id"],body["seq"],stamp,received,source,ts_suspect,
                     Jsonb({"door_state":door_state}),Jsonb(body)))
                inserted=cur.rowcount
                if inserted:
                    update_device_presence(cur, device_id=device, boot_id=body["boot_id"], received_at=received)
                    event_types=load_profile_event_types(cur,profile,"door_state")
                    heartbeat_id,heartbeat_type=door_heartbeat_event(profile,body,event_types)
                    if not ts_suspect:
                        on_event(cur,device_id=device,event_id=heartbeat_id,event_type=heartbeat_type,
                                 received_at=received,payload=body,observed_at=stamp)
                conn.commit()
                LOG.info("SAVED heartbeat device=%s inserted=%d",device,inserted)
                return inserted
            quality, metrics = validate_telemetry_data(body, point_definitions)
            cur.execute("""INSERT INTO telemetry_reports
                (device_id,boot_id,seq,quality,measured_at,received_at,ts_source,ts_suspect,payload_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (device_id,boot_id,seq) DO NOTHING""",
                (device, body["boot_id"], body["seq"], quality, stamp, received, source,
                 ts_suspect, Jsonb(body)))
            if not cur.rowcount:
                conn.commit()
                return 0
            inserted = 1
            update_device_presence(cur, device_id=device, boot_id=body["boot_id"], received_at=received)
            for metric, value in metrics.items():
                cur.execute("""INSERT INTO telemetry
                    (device_id, boot_id, seq, metric, value_number, measured_at, received_at, ts_source,ts_suspect)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (device_id, metric, measured_at) DO NOTHING""",
                    (device, body["boot_id"], body["seq"], metric, value, stamp, received, source,ts_suspect))
                new_count = cur.rowcount
                inserted += new_count
                if new_count and quality == "good" and not ts_suspect:
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
            (device_id,event_id,event_type,boot_id,seq,event_at,received_at,ts_source,ts_suspect,event_source,payload_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'device',%s) ON CONFLICT (device_id, event_id) DO NOTHING""",
                (device, body["event_id"], event_type, body["boot_id"], body["seq"], stamp,
                 received, source, ts_suspect, Jsonb(body)))
            inserted = cur.rowcount
            if inserted:
                update_device_presence(cur, device_id=device, boot_id=body["boot_id"], received_at=received)
                if not ts_suspect:
                    on_event(cur, device_id=device, event_id=body["event_id"],
                             event_type=event_type, received_at=received, payload=body,
                             observed_at=stamp)
        else:
            state = body["state"]
            inserted = update_device_presence(cur, device_id=device, boot_id=body["boot_id"],
                                              received_at=received, explicit_state=state)
            points = load_profile_contract(cur, profile, "status")
            event_types = load_profile_event_types(cur, profile, "door_state")
            heartbeat = door_heartbeat_event(profile, body, event_types) if "door_state" in points else None
            if heartbeat:
                cur.execute("""INSERT INTO device_state_snapshots
                    (device_id,boot_id,seq,measured_at,received_at,ts_source,ts_suspect,state_json,payload_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (device_id,boot_id,seq) DO NOTHING""",
                    (device, body["boot_id"], body["seq"], stamp, received, source,ts_suspect,
                     Jsonb({"door_state": body["data"]["door_state"]}), Jsonb(body)))
                heartbeat_id, heartbeat_type = heartbeat
                if not ts_suspect:
                    on_event(cur, device_id=device, event_id=heartbeat_id,
                             event_type=heartbeat_type, received_at=received, payload=body,
                             observed_at=stamp)
        conn.commit()
        LOG.info("SAVED %s device=%s inserted=%d", channel, device, inserted)
        return inserted
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
    started = time.monotonic()
    org = "unknown"
    try:
        if len(message.payload) > MAX_MQTT_PAYLOAD_BYTES:
            raise ValueError("E-SIZE payload exceeds 512 bytes")
        parts = message.topic.split("/")
        if len(parts) == 6:
            org = parts[2]
        record_ingestion_metric(org, "received")
        if len(parts) == 6 and not RATE_LIMITER.allow(parts[4]):
            raise ValueError("E-RATE device message rate exceeded")
        body = json.loads(message.payload.decode("utf-8"))
        inserted = save_message(message.topic, body)
        latency = (time.monotonic() - started) * 1000.0
        record_ingestion_metric(org, "duplicate" if inserted == 0 else "valid", latency_ms=latency)
    except (ValueError, KeyError, TypeError, psycopg.Error) as exc:
        code = str(exc).split(" ", 1)[0][:48]
        record_ingestion_metric(org, "rejected", error_code=code,
                                latency_ms=(time.monotonic() - started) * 1000.0)
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
        except (OSError, ssl.SSLError, paho_mqtt.MQTTException) as exc:
            LOG.warning("MQTT unavailable (%s), retrying", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
