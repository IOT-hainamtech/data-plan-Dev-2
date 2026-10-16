"""Simulates independent ESP32 S1 temperature/humidity and S6 door sensor devices."""
import json
import os
import random
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

ORG = "demo"  # DEMO ONLY: must match nextGRP registry at integration time.
SITE = "demo-site"
HOST = os.getenv("MQTT_HOST", "mqtt")
PORT = int(os.getenv("MQTT_PORT", "8883"))
CA_FILE = os.getenv("MQTT_CA_FILE", "/run/mqtt/ca.crt")
INTERVAL = max(1, int(os.getenv("SIM_INTERVAL", "10")))  # Short demo interval; S1 spec is 30–60 s.


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SimulatedDevice:
    def __init__(self, device_id):
        self.device_id = device_id
        self.boot_id = uuid.uuid4().hex[:10]
        self.seq = 0
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"sim-{device_id}-{self.boot_id}")
        prefix = device_id.replace("-", "")
        self.client.username_pw_set(os.environ[f"{prefix}_MQTT_USERNAME"],
                                    os.environ[f"{prefix}_MQTT_PASSWORD"])
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=CA_FILE)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        self.client.tls_set_context(context)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.status_topic = self.topic("status")
        will_payload = self.message({}, state="offline", increment=False)
        self.client.will_set(self.status_topic, json.dumps(will_payload), qos=1, retain=True)
        self.client.connect_async(HOST, PORT, keepalive=45)
        self.client.loop_start()
        if not self.connected.wait(timeout=20):
            raise ConnectionError(f"MQTT connection timeout for {device_id}")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"SIM {self.device_id} connection rejected: {reason_code}", flush=True)
            return
        self.connected.set()
        self.publish("status", self.message({}, state="online"), retain=True, wait=False)
        print(f"SIM {self.device_id} connected with TLS", flush=True)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.connected.clear()
        if reason_code.is_failure:
            print(f"SIM {self.device_id} disconnected; reconnecting: {reason_code}", flush=True)

    def topic(self, channel):
        return f"nw/v1/{ORG}/{SITE}/{self.device_id}/{channel}"

    def message(self, data, state=None, event_type=None, increment=True):
        if increment:
            self.seq += 1
        payload = {
            "schema_version": 1, "device_id": self.device_id,
            "boot_id": self.boot_id, "ts": timestamp(), "seq": self.seq,
            "data": data,
        }
        if state:
            payload["state"] = state
        if event_type:
            payload["event_type"] = event_type
            payload["event_id"] = f"{self.device_id}-{self.boot_id}-{self.seq}"
        return payload

    def publish(self, channel, payload, retain=False, wait=True):
        info = self.client.publish(self.topic(channel), json.dumps(payload), qos=1, retain=retain)
        if wait:
            info.wait_for_publish(timeout=10)
        if wait and not info.is_published():
            raise TimeoutError(f"MQTT QoS 1 publish timeout for {self.device_id}")
        print(f"SIM {self.device_id} {channel}: {json.dumps(payload, ensure_ascii=False)}", flush=True)


def main():
    # Allow Data Plane to connect/subscribe before sending the first non-retained event.
    time.sleep(3)
    s1 = SimulatedDevice("TH-001")
    s6 = SimulatedDevice("DR-001")
    tick = 0
    door_open = False
    try:
        while True:
            temperature = round(random.uniform(25.0, 33.0), 1)
            humidity = round(random.uniform(45.0, 70.0), 1)
            s1.publish("telemetry", s1.message({"temperature": temperature, "humidity": humidity}))
            if tick % 4 == 0:
                door_open = not door_open
                state = "open" if door_open else "closed"
                event_type = "DOOR_OPENED" if door_open else "DOOR_CLOSED"
                s6.publish("event", s6.message({"door_state": state}, event_type=event_type))
            if tick > 0 and tick * INTERVAL % 60 == 0:
                s1.publish("status", s1.message({}, state="online"), retain=True)
                s6.publish("status", s6.message({}, state="online"), retain=True)
            tick += 1
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        for device in (s1, s6):
            device.publish("status", device.message({}, state="offline"), retain=True)
            device.client.disconnect()
            device.client.loop_stop()


if __name__ == "__main__":
    main()
