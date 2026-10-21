"""Multi-device integration tests verifying TH-001 and TH-002 coexistence, rule isolation, and ACLs."""
import json
import os
import ssl
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt
import psycopg

from ingestor import db_connection, save_message


def tls_context(cafile):
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def connected_client(username, password, cafile):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"multi-dev-{uuid.uuid4().hex[:8]}",
                         protocol=mqtt.MQTTv311)
    client.username_pw_set(username, password)
    client.tls_set_context(tls_context(cafile))
    ready, result = threading.Event(), {}
    def on_connect(_c, _u, _f, reason_code, _p):
        result["reason"] = reason_code
        ready.set()
    client.on_connect = on_connect
    client.connect(os.getenv("MQTT_HOST", "mqtt"), int(os.getenv("MQTT_PORT", "8883")), 10)
    client.loop_start()
    if not ready.wait(8):
        client.loop_stop()
        raise TimeoutError("MQTT CONNACK timeout")
    return client, result["reason"]


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class MultiDeviceDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.boot_th1 = f"boot-th1-{uuid.uuid4().hex[:6]}"
        self.boot_th2 = f"boot-th2-{uuid.uuid4().hex[:6]}"
        offset = uuid.uuid4().int % 1000000
        self.t_base = datetime(2036, 5, 1, 0, 0, 0) + timedelta(seconds=offset)
        self.clean_db()

    def clean_db(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE rule_code IN ('S1_TEMP_HIGH','S1_TH002_TEMP_HIGH'))")
                cur.execute("DELETE FROM alarm_occurrences WHERE rule_code IN ('S1_TEMP_HIGH','S1_TH002_TEMP_HIGH')")
                cur.execute("DELETE FROM rule_runtime_state WHERE rule_code IN ('S1_TEMP_HIGH','S1_TH002_TEMP_HIGH')")
                cur.execute("DELETE FROM telemetry WHERE boot_id IN (%s, %s)", (self.boot_th1, self.boot_th2))
                cur.execute("DELETE FROM telemetry_reports WHERE boot_id IN (%s, %s)", (self.boot_th1, self.boot_th2))
            conn.commit()

    def tearDown(self):
        self.clean_db()

    def test_th001_and_th002_isolated_ingestion_and_storage(self):
        ts = self.t_base.isoformat() + "Z"
        # TH-001 sends telemetry
        save_message("nw/v1/demo/demo-site/TH-001/telemetry", {
            "schema_version": 1, "device_id": "TH-001", "boot_id": self.boot_th1,
            "seq": 1, "ts": ts, "quality": "good",
            "data": {"temperature": 24.5, "humidity": 50.0}
        })
        # TH-002 sends telemetry with its own boot_id and seq
        save_message("nw/v1/demo/demo-site/TH-002/telemetry", {
            "schema_version": 1, "device_id": "TH-002", "boot_id": self.boot_th2,
            "seq": 1, "ts": ts, "quality": "good",
            "data": {"temperature": 28.2, "humidity": 62.0}
        })

        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT device_id, value_number FROM telemetry WHERE metric='temperature' AND boot_id IN (%s, %s)",
                            (self.boot_th1, self.boot_th2))
                readings = dict(cur.fetchall())
                self.assertEqual(readings.get("TH-001"), 24.5)
                self.assertEqual(readings.get("TH-002"), 28.2)

    def test_th001_alarm_does_not_affect_th002(self):
        # 1. Trigger alarm on TH-001: continuous samples every 60s for 300s
        for s in (0, 60, 120, 180, 240, 300):
            ts = (self.t_base + timedelta(seconds=s)).isoformat() + "Z"
            save_message("nw/v1/demo/demo-site/TH-001/telemetry", {
                "schema_version": 1, "device_id": "TH-001", "boot_id": self.boot_th1,
                "seq": s // 60 + 1, "ts": ts, "quality": "good",
                "data": {"temperature": 32.0, "humidity": 50.0}
            })

        # 2. TH-002 stays normal at 26°C
        t_end = (self.t_base + timedelta(seconds=300)).isoformat() + "Z"
        save_message("nw/v1/demo/demo-site/TH-002/telemetry", {
            "schema_version": 1, "device_id": "TH-002", "boot_id": self.boot_th2,
            "seq": 1, "ts": t_end, "quality": "good",
            "data": {"temperature": 26.0, "humidity": 50.0}
        })

        with db_connection() as conn:
            with conn.cursor() as cur:
                # Check alarm on TH-001
                cur.execute("SELECT count(*) FROM alarm_occurrences WHERE device_id='TH-001' AND occurrence_kind='TRIGGERED'")
                th1_alarms = cur.fetchone()[0]
                self.assertEqual(th1_alarms, 1)

                # Check NO alarm on TH-002
                cur.execute("SELECT count(*) FROM alarm_occurrences WHERE device_id='TH-002'")
                th2_alarms = cur.fetchone()[0]
                self.assertEqual(th2_alarms, 0)


@unittest.skipUnless(os.getenv("RUN_MQTT_TESTS") == "1", "set RUN_MQTT_TESTS=1")
class MultiDeviceMqttAclTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca = os.environ["MQTT_CA_FILE"]
        cls.th1_user = os.environ["TH001_MQTT_USERNAME"]
        cls.th1_pass = os.environ["TH001_MQTT_PASSWORD"]
        cls.th2_user = os.environ["TH002_MQTT_USERNAME"]
        cls.th2_pass = os.environ["TH002_MQTT_PASSWORD"]
        cls.reader_user = os.getenv("MQTT_TEST_READER_USERNAME", os.environ["MQTT_USERNAME"])
        cls.reader_pass = os.getenv("MQTT_TEST_READER_PASSWORD", os.environ["MQTT_PASSWORD"])

    def test_th001_and_th002_cannot_publish_to_each_others_topics(self):
        reader, reason = connected_client(self.reader_user, self.reader_pass, self.ca)
        self.assertFalse(reason.is_failure)
        received, signal = [], threading.Event()
        reader.on_message = lambda _c, _u, message: (received.append(message.payload), signal.set())
        reader.subscribe("nw/v1/demo/demo-site/+/+", qos=1)
        time.sleep(0.2)

        # Cross-device publish attempts:
        # 1. TH-001 tries to publish to TH-002 topic
        c1, reason1 = connected_client(self.th1_user, self.th1_pass, self.ca)
        self.assertFalse(reason1.is_failure)
        marker1 = b"th1-trespass-th2"
        c1.publish("nw/v1/demo/demo-site/TH-002/telemetry", marker1, qos=1)
        c1.disconnect(); c1.loop_stop()
        signal.wait(0.5); signal.clear()
        self.assertNotIn(marker1, received)

        # 2. TH-002 tries to publish to TH-001 topic
        c2, reason2 = connected_client(self.th2_user, self.th2_pass, self.ca)
        self.assertFalse(reason2.is_failure)
        marker2 = b"th2-trespass-th1"
        c2.publish("nw/v1/demo/demo-site/TH-001/telemetry", marker2, qos=1)
        c2.disconnect(); c2.loop_stop()
        signal.wait(0.5); signal.clear()
        self.assertNotIn(marker2, received)

        # 3. TH-002 publishes to its OWN topic -> succeeds and reader receives it!
        c2_legit, reason2_legit = connected_client(self.th2_user, self.th2_pass, self.ca)
        self.assertFalse(reason2_legit.is_failure)
        legit_marker = b"th2-legit-telemetry"
        c2_legit.publish("nw/v1/demo/demo-site/TH-002/telemetry", legit_marker, qos=1)
        c2_legit.disconnect(); c2_legit.loop_stop()
        signal.wait(1.5)
        self.assertIn(legit_marker, received)

        reader.disconnect(); reader.loop_stop()


if __name__ == "__main__":
    unittest.main()
