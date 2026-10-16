"""Live MQTT TLS/auth/ACL/QoS/LWT tests. Enable with RUN_MQTT_TESTS=1."""
import json
import os
import ssl
import threading
import time
import unittest
import uuid
import warnings
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def tls_context(cafile):
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def connected_client(username, password, cafile, client_id=None, will=None):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=client_id or f"mqtt-test-{uuid.uuid4().hex}",
                         protocol=mqtt.MQTTv5)
    client.username_pw_set(username, password)
    client.tls_set_context(tls_context(cafile))
    if will:
        client.will_set(*will)
    ready, result = threading.Event(), {}
    def on_connect(_client, _userdata, _flags, reason_code, _properties):
        result["reason"] = reason_code
        ready.set()
    client.on_connect = on_connect
    client.connect(os.getenv("MQTT_HOST", "mqtt"), int(os.getenv("MQTT_PORT", "8883")), 10)
    client.loop_start()
    if not ready.wait(8):
        client.loop_stop()
        raise TimeoutError("MQTT CONNACK timeout")
    return client, result["reason"]


@unittest.skipUnless(os.getenv("RUN_MQTT_TESTS") == "1", "set RUN_MQTT_TESTS=1")
class MqttSecurityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca = os.environ["MQTT_CA_FILE"]
        cls.th_user = os.environ["TH001_MQTT_USERNAME"]
        cls.th_password = os.environ["TH001_MQTT_PASSWORD"]
        cls.reader_user = os.getenv("MQTT_TEST_READER_USERNAME", os.environ["MQTT_USERNAME"])
        cls.reader_password = os.getenv("MQTT_TEST_READER_PASSWORD", os.environ["MQTT_PASSWORD"])

    def test_valid_account_and_qos1(self):
        client, reason = connected_client(self.th_user, self.th_password, self.ca)
        self.assertFalse(reason.is_failure)
        info = client.publish("nw/v1/demo/demo-site/TH-001/telemetry", "{}", qos=1)
        info.wait_for_publish(5)
        self.assertTrue(info.is_published())
        client.disconnect(); client.loop_stop()

    def test_wrong_password_is_rejected(self):
        client, reason = connected_client(self.th_user, "definitely-wrong", self.ca)
        self.assertTrue(reason.is_failure)
        client.disconnect(); client.loop_stop()

    def test_untrusted_ca_is_rejected(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"bad-ca-{uuid.uuid4().hex}")
        client.username_pw_set(self.th_user, self.th_password)
        client.tls_set_context(tls_context("/etc/ssl/certs/ca-certificates.crt"))
        rejected = False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            try:
                client.connect(os.getenv("MQTT_HOST", "mqtt"), int(os.getenv("MQTT_PORT", "8883")), 10)
            except (ssl.SSLError, OSError):
                rejected = True
            finally:
                client._sock_close()
        self.assertTrue(rejected)

    def test_acl_rejects_cross_device_publish(self):
        client, reason = connected_client(self.th_user, self.th_password, self.ca)
        self.assertFalse(reason.is_failure)
        published, outcome = threading.Event(), {}
        def on_publish(_client, _userdata, mid, reason_code, _properties):
            outcome["reason"] = reason_code
            published.set()
        client.on_publish = on_publish
        client.publish("nw/v1/demo/demo-site/DR-001/event", "{}", qos=1)
        self.assertTrue(published.wait(5))
        self.assertTrue(outcome["reason"].is_failure)
        client.disconnect(); client.loop_stop()

    def test_retained_status_and_lwt(self):
        topic = "nw/v1/demo/demo-site/TH-001/status"
        boot = f"mqtt-test-{uuid.uuid4().hex[:8]}"
        offline = json.dumps({"schema_version": 1, "device_id": "TH-001", "boot_id": boot,
                              "ts": stamp(), "seq": 1, "state": "offline", "data": {}})
        online = json.dumps({"schema_version": 1, "device_id": "TH-001", "boot_id": boot,
                             "ts": stamp(), "seq": 2, "state": "online", "data": {}})
        subscriber, reason = connected_client(self.reader_user, self.reader_password, self.ca)
        self.assertFalse(reason.is_failure)
        received = []
        signal = threading.Event()
        def on_message(_client, _userdata, message):
            received.append((json.loads(message.payload), message.retain))
            signal.set()
        subscriber.on_message = on_message
        subscriber.subscribe(topic, qos=1)

        publisher, reason = connected_client(
            self.th_user, self.th_password, self.ca, f"lwt-{uuid.uuid4().hex}",
            will=(topic, offline, 1, True))
        self.assertFalse(reason.is_failure)
        publisher.publish(topic, online, qos=1, retain=True).wait_for_publish(5)
        signal.wait(5); signal.clear()
        publisher.loop_stop()
        publisher._sock_close()
        deadline = time.time() + 8
        while time.time() < deadline and not any(item[0].get("state") == "offline" for item in received):
            signal.wait(1); signal.clear()
        self.assertTrue(any(item[0].get("state") == "offline" for item in received))

        restore, reason = connected_client(self.th_user, self.th_password, self.ca)
        self.assertFalse(reason.is_failure)
        restore.publish(topic, online, qos=1, retain=True).wait_for_publish(5)
        restore.disconnect(); restore.loop_stop()
        subscriber.disconnect(); subscriber.loop_stop()

        retained, reason = connected_client(self.reader_user, self.reader_password, self.ca)
        retained_message, retained_signal = {}, threading.Event()
        def retained_callback(_client, _userdata, message):
            retained_message["state"] = json.loads(message.payload)["state"]
            retained_message["retain"] = message.retain
            retained_signal.set()
        retained.on_message = retained_callback
        retained.subscribe(topic, qos=1)
        self.assertTrue(retained_signal.wait(5))
        self.assertEqual(retained_message, {"state": "online", "retain": True})
        retained.disconnect(); retained.loop_stop()


if __name__ == "__main__":
    unittest.main()
