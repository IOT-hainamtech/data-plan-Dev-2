"""nextWireless M1 ingestion with authenticated TLS MQTT transport."""
import json
import logging
import os
import ssl
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

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
from realtime import enqueue_device_event, enqueue_telemetry_event
from status import (reconcile_device_timeouts, reconcile_fast_presence_timeouts,
                    update_device_presence, update_fast_presence)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("data-plane")
MAX_MQTT_PAYLOAD_BYTES = int(os.getenv("MQTT_MAX_PAYLOAD_BYTES", "512"))


class DeviceRateLimiter:
    """Token bucket: burst five messages, sustained refill one message/second."""
    def __init__(self, capacity=5.0, refill_per_second=1.0):
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self._state = defaultdict(lambda: [self.capacity, time.monotonic()])
        self._lock = threading.Lock()

    def allow(self, device_id):
        now = time.monotonic()
        with self._lock:
            state = self._state[device_id]
            elapsed = now - state[1]
            state[0] = min(self.capacity, state[0] + elapsed * self.refill_per_second)
            state[1] = now
            if state[0] < 1.0:
                return False
            state[0] -= 1.0
            return True


# A nominal one-second ESP32 schedule can arrive slightly faster than one
# message per wall-clock second because of oscillator and network jitter. Keep
# a narrow 25% margin while retaining the five-message burst ceiling.
RATE_LIMITER = DeviceRateLimiter(
    capacity=5.0,
    refill_per_second=float(os.getenv("MQTT_DEVICE_RATE_PER_SECOND", "1.25")),
)
PRESENCE_RATE_LIMITER = DeviceRateLimiter(capacity=5.0, refill_per_second=5.0)
PRESENCE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mqtt-presence")
BUSINESS_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mqtt-business")


def rate_limit_key(device_id, body):
    """Keep 1 Hz presence independent from lower-frequency business traffic."""
    return f"{device_id}:presence" if body.get("msg_kind") == "presence" else device_id


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
            if quality == "good" and not ts_suspect:
                enqueue_telemetry_event(cur, org_code=org, device_id=device,
                    boot_id=body["boot_id"], seq=body["seq"], measured_at=stamp,
                    metrics=metrics, point_definitions=point_definitions, created_at=received)
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
                    enqueue_device_event(cur, org_code=org, device_id=device,
                        event_id=body["event_id"], event_type=event_type, event_at=stamp,
                        door_state=body["data"].get("door_state"), created_at=received)
                    on_event(cur, device_id=device, event_id=body["event_id"],
                             event_type=event_type, received_at=received, payload=body,
                             observed_at=stamp)
        else:
            state = body["state"]
            if body.get("msg_kind") == "presence":
                allowed = {item.strip() for item in os.getenv("FAST_PRESENCE_DEVICE_IDS", "").split(",")
                           if item.strip()}
                if device not in allowed:
                    # A device may still run firmware that emits presence after
                    # server-side opt-out. Ignore it without status mutation,
                    # durable metrics, or warning-log noise.
                    LOG.debug("IGNORED presence device=%s fast presence disabled", device)
                    return 0
                inserted = update_fast_presence(cur, device_id=device, boot_id=body["boot_id"],
                                                seq=body["seq"], received_at=received)
                conn.commit()
                LOG.debug("SAVED presence device=%s accepted=%d", device, inserted)
                return inserted
            inserted = update_device_presence(cur, device_id=device, boot_id=body["boot_id"],
                                              received_at=received, explicit_state=state,
                                              seq=body["seq"])
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


def process_message(topic, body, started):
    org = "unknown"
    try:
        parts = topic.split("/")
        if len(parts) == 6:
            org = parts[2]
        presence = body.get("msg_kind") == "presence"
        if not presence:
            record_ingestion_metric(org, "received")
        limiter = PRESENCE_RATE_LIMITER if body.get("msg_kind") == "presence" else RATE_LIMITER
        if len(parts) == 6 and not limiter.allow(rate_limit_key(parts[4], body)):
            raise ValueError("E-RATE device message rate exceeded")
        inserted = save_message(topic, body)
        latency = (time.monotonic() - started) * 1000.0
        if not presence:
            record_ingestion_metric(org, "duplicate" if inserted == 0 else "valid", latency_ms=latency)
    except (ValueError, KeyError, TypeError, psycopg.Error) as exc:
        code = str(exc).split(" ", 1)[0][:48]
        if body.get("msg_kind") != "presence":
            record_ingestion_metric(org, "rejected", error_code=code,
                                    latency_ms=(time.monotonic() - started) * 1000.0)
        LOG.warning("REJECTED topic=%s reason=%s", topic, exc)
    except Exception:
        LOG.exception("Unexpected processing failure topic=%s", topic)


def on_message(client, userdata, message):
    """Keep the Paho network loop non-blocking and isolate presence latency."""
    started = time.monotonic()
    try:
        if len(message.payload) > MAX_MQTT_PAYLOAD_BYTES:
            raise ValueError("E-SIZE payload exceeds 512 bytes")
        body = json.loads(message.payload.decode("utf-8"))
        executor = PRESENCE_EXECUTOR if body.get("msg_kind") == "presence" else BUSINESS_EXECUTOR
        executor.submit(process_message, message.topic, body, started)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOG.warning("REJECTED topic=%s reason=%s", message.topic, exc)


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


def fast_presence_timer(stop_event):
    devices = {item.strip() for item in os.getenv("FAST_PRESENCE_DEVICE_IDS", "").split(",")
               if item.strip()}
    if not devices:
        LOG.info("Fast Presence disabled (FAST_PRESENCE_DEVICE_IDS is empty)")
        return
    interval = max(0.1, float(os.getenv("FAST_PRESENCE_RECONCILE_SECONDS", "0.5")))
    timeout = max(1.0, float(os.getenv("FAST_PRESENCE_TIMEOUT_SECONDS", "4")))
    LOG.info("Fast Presence enabled devices=%s timeout=%.3fs reconcile=%.3fs",
             ",".join(sorted(devices)), timeout, interval)
    while not stop_event.wait(interval):
        try:
            now = now_utc()
            with db_connection() as conn:
                with conn.cursor() as cur:
                    changes = reconcile_fast_presence_timeouts(cur, now, devices, timeout)
                conn.commit()
            if changes:
                LOG.info("Fast Presence marked %d device(s) offline", changes)
        except Exception:
            LOG.exception("Fast Presence reconciliation failed")


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
    presence_timer = threading.Thread(target=fast_presence_timer, args=(stop_event,),
                                      name="fast-presence-timer", daemon=True)
    presence_timer.start()
    while True:
        try:
            client.connect(mqtt_host, mqtt_port, keepalive=60)
            client.loop_forever(retry_first_connection=True)
        except (OSError, ssl.SSLError, paho_mqtt.MQTTException) as exc:
            LOG.warning("MQTT unavailable (%s), retrying", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
