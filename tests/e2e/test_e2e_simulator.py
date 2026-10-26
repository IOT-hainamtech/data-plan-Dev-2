"""End-to-end MQTT, ingestion, database, rules, outbox and mock nextGRP tests."""
import json
import os
import ssl
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

from core.clock import now_utc
from core.database import db_connection
from integrations.nextgrp import NextGrpAdapter
from outbox.worker import OutboxStore, OutboxWorker


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def tls_context(cafile):
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def connected_client(username, password, cafile, client_id=None, will=None):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=client_id or f"e2e-sim-{uuid.uuid4().hex[:8]}",
                         protocol=mqtt.MQTTv311)
    client.username_pw_set(username, password)
    client.tls_set_context(tls_context(cafile))
    if will:
        client.will_set(*will)
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


@unittest.skipUnless(os.getenv("RUN_E2E_TESTS") == "1", "set RUN_E2E_TESTS=1")
class EndToEndSimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca = os.environ["MQTT_CA_FILE"]
        cls.th_user = os.environ["TH001_MQTT_USERNAME"]
        cls.th_pass = os.environ["TH001_MQTT_PASSWORD"]
        cls.th2_user = os.environ["TH002_MQTT_USERNAME"]
        cls.th2_pass = os.environ["TH002_MQTT_PASSWORD"]
        cls.dr_user = os.environ["DR001_MQTT_USERNAME"]
        cls.dr_pass = os.environ["DR001_MQTT_PASSWORD"]
        cls.mock_url = os.getenv("MOCK_TEST_URL", "http://mock-nextgrp:8080/mock/v1/alarms")
        cls.mock_token = os.getenv("MOCK_TEST_TOKEN", "mock-development-token")

    def setUp(self):
        self.boot_id = f"e2e-{uuid.uuid4().hex[:8]}"
        self.alarm_keys = []
        self.s6_runtime_snapshot = None
        self.s6_schedule_snapshot = None
        self.store = OutboxStore()
        self.adapter = NextGrpAdapter(
            self.mock_url, timeout=5, auth_mode="bearer", token=self.mock_token,
            allow_insecure_mock=True)
        self.worker = OutboxWorker(self.store, self.adapter, worker_id="e2e-test-worker")

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                for alarm_key in self.alarm_keys:
                    cur.execute("DELETE FROM alarm_outbox WHERE alarm_key=%s", (alarm_key,))
                    cur.execute("DELETE FROM alarm_occurrences WHERE alarm_key=%s", (alarm_key,))
                cur.execute("DELETE FROM telemetry WHERE boot_id=%s", (self.boot_id,))
                cur.execute("DELETE FROM telemetry_reports WHERE boot_id=%s", (self.boot_id,))
                cur.execute("DELETE FROM device_events WHERE boot_id=%s", (self.boot_id,))
                cur.execute("DELETE FROM rule_runtime_state WHERE rule_code='S1_TH002_TEMP_HIGH' AND device_id='TH-002'")
                if self.s6_runtime_snapshot is not None:
                    cur.execute("""UPDATE rule_runtime_state SET
                        hold_started_at=%s,is_active=%s,last_sample_at=%s,last_fired_at=%s,
                        recovery_started_at=%s,episode_key=%s,episode_started_at=%s,
                        current_state=%s,state_changed_at=%s,source_event_id=%s
                        WHERE rule_code='S6_DOOR_OPEN' AND device_id='DR-001'""",
                        self.s6_runtime_snapshot)
                if self.s6_schedule_snapshot is not None:
                    cur.execute("""UPDATE rule_definitions SET business_start=%s,
                        business_end=%s, schedule_timezone=%s
                        WHERE rule_code='S6_DOOR_OPEN'""", self.s6_schedule_snapshot)
            conn.commit()

    def test_e2e_s1_telemetry_flow_and_outbox_delivery(self):
        # TH-002 is the isolated S1 test path. Six samples spaced by 60 seconds
        # prove the full 300-second hold without waiting five wall-clock minutes.
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rule_runtime_state WHERE rule_code='S1_TH002_TEMP_HIGH' AND device_id='TH-002'")
            conn.commit()
        client, reason = connected_client(self.th2_user, self.th2_pass, self.ca)
        self.assertFalse(reason.is_failure)
        topic = "nw/v1/demo/demo-site/TH-002/telemetry"
        base = datetime.now(timezone.utc).replace(microsecond=0)
        received_after = now_utc()
        for seq, seconds in enumerate((0, 60, 120, 180, 240, 300), start=1):
            body = {
                "schema_version": 1, "device_id": "TH-002", "boot_id": self.boot_id,
                "seq": seq,
                "ts": (base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
                "quality": "good", "data": {"temperature": 31.5, "humidity": 52.0}
            }
            info = client.publish(topic, json.dumps(body), qos=1)
            info.wait_for_publish(5)
            self.assertTrue(info.is_published())
        client.disconnect(); client.loop_stop()

        # Wait for all reports and the S1 occurrence to be persisted.
        deadline = time.monotonic() + 8
        alarm_key = None
        while time.monotonic() < deadline:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM telemetry_reports WHERE boot_id=%s", (self.boot_id,))
                    report_count = cur.fetchone()[0]
                    cur.execute("""SELECT alarm_key FROM alarm_occurrences
                        WHERE rule_code='S1_TH002_TEMP_HIGH' AND device_id='TH-002'
                          AND occurrence_kind='TRIGGERED' AND triggered_at >= %s
                        ORDER BY id DESC LIMIT 1""", (received_after,))
                    row = cur.fetchone()
                    if report_count == 6 and row:
                        alarm_key = row[0]
                        break
            time.sleep(0.25)
        self.assertIsNotNone(alarm_key, "S1 alarm was not created after 300 continuous seconds")
        self.alarm_keys.append(alarm_key)

        # The separately running outbox-worker service must deliver this row.
        deadline = time.monotonic() + 5
        delivery_state = ack_id = delivery_event_id = None
        while time.monotonic() < deadline:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT delivery_state,ack_id,delivery_event_id FROM alarm_outbox WHERE alarm_key=%s", (alarm_key,))
                    delivery_state, ack_id, delivery_event_id = cur.fetchone()
            if delivery_state == "DELIVERED":
                break
            time.sleep(0.1)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_state, last_seen_at FROM device_status WHERE device_id='TH-002'")
                state, last_seen = cur.fetchone()
                self.assertEqual(state, "online")
                self.assertIsNotNone(last_seen)
                self.assertEqual(delivery_state, "DELIVERED")
                self.assertTrue(bool(ack_id))

        # Drive the same episode through its deterministic clear transition.
        client, reason = connected_client(self.th2_user, self.th2_pass, self.ca)
        self.assertFalse(reason.is_failure)
        for seq, seconds in enumerate((301, 361, 421), start=7):
            body = {"schema_version": 1, "device_id": "TH-002", "boot_id": self.boot_id,
                    "seq": seq, "ts": (base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
                    "quality": "good", "data": {"temperature": 28.0, "humidity": 52.0}}
            client.publish(topic, json.dumps(body), qos=1).wait_for_publish(5)
        client.disconnect(); client.loop_stop()
        clear_key = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""SELECT a.alarm_key,o.delivery_state FROM alarm_occurrences a
                        JOIN alarm_outbox o ON o.alarm_key=a.alarm_key
                        WHERE a.rule_code='S1_TH002_TEMP_HIGH' AND a.device_id='TH-002'
                          AND a.occurrence_kind='CLEARED' AND a.triggered_at >= %s
                        ORDER BY a.id DESC LIMIT 1""", (received_after,))
                    row = cur.fetchone()
                    if row and row[1] == "DELIVERED":
                        clear_key = row[0]
                        break
            time.sleep(0.2)
        self.assertIsNotNone(clear_key, "S1 cleared event was not delivered by outbox-worker")
        self.alarm_keys.append(clear_key)

        # Simulate at-least-once replay for this test event only. The real worker
        # must receive the same ACK and Mock inbox must keep one event_id row.
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE alarm_outbox SET delivery_state='PENDING',next_attempt_at=now() at time zone 'utc',
                    delivered_at=NULL,acked_at=NULL,locked_by=NULL,locked_until=NULL WHERE alarm_key=%s""", (alarm_key,))
            conn.commit()
        deadline = time.monotonic() + 5
        replay_ack = None
        while time.monotonic() < deadline:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT delivery_state,ack_id FROM alarm_outbox WHERE alarm_key=%s", (alarm_key,))
                    state, replay_ack = cur.fetchone()
                    cur.execute("SELECT count(*) FROM bridge_event_inbox WHERE event_id=%s", (str(delivery_event_id),))
                    inbox_count = cur.fetchone()[0]
            if state == "DELIVERED":
                break
            time.sleep(0.1)
        self.assertEqual(state, "DELIVERED")
        self.assertEqual(replay_ack, ack_id)
        self.assertEqual(inbox_count, 1)

    def test_e2e_s6_door_event_and_outbox_ack(self):
        # Snapshot and temporarily isolate the durable demo runtime while the
        # simulator is stopped, so an existing quiet window cannot mask E2E.
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT hold_started_at,is_active,last_sample_at,last_fired_at,
                    recovery_started_at,episode_key,episode_started_at,current_state,
                    state_changed_at,source_event_id FROM rule_runtime_state
                    WHERE rule_code='S6_DOOR_OPEN' AND device_id='DR-001' FOR UPDATE""")
                self.s6_runtime_snapshot = cur.fetchone()
                cur.execute("""SELECT business_start,business_end,schedule_timezone
                    FROM rule_definitions WHERE rule_code='S6_DOOR_OPEN' FOR UPDATE""")
                self.s6_schedule_snapshot = cur.fetchone()
                # An equal start/end is an empty business-hours interval. This
                # keeps the outside-hours E2E path deterministic at any wall-clock time.
                cur.execute("""UPDATE rule_definitions SET business_start=TIME '00:00:00',
                    business_end=TIME '00:00:00'
                    WHERE rule_code='S6_DOOR_OPEN'""")
                cur.execute("""UPDATE rule_runtime_state SET current_state='closed',
                    state_changed_at=NULL,is_active=FALSE,last_fired_at=NULL,
                    recovery_started_at=NULL,episode_key=NULL,episode_started_at=NULL,
                    source_event_id=NULL
                    WHERE rule_code='S6_DOOR_OPEN' AND device_id='DR-001'""")
            conn.commit()

        client, reason = connected_client(self.dr_user, self.dr_pass, self.ca)
        self.assertFalse(reason.is_failure)
        topic = "nw/v1/demo/demo-site/DR-001/event"
        event_id = f"DR-001-{self.boot_id}-1"
        body = {
            "schema_version": 1, "device_id": "DR-001", "boot_id": self.boot_id,
            "seq": 1, "ts": stamp(), "event_type": "DOOR_OPENED", "event_id": event_id,
            "data": {"door_state": "open"}
        }
        info = client.publish(topic, json.dumps(body), qos=1)
        info.wait_for_publish(5)
        self.assertTrue(info.is_published())
        client.disconnect(); client.loop_stop()

        # Wait for device_events table
        deadline = time.monotonic() + 8
        found = False
        while time.monotonic() < deadline:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM device_events WHERE event_id=%s", (event_id,))
                    if cur.fetchone()[0] == 1:
                        found = True
                        break
            time.sleep(0.25)
        self.assertTrue(found, "Door event was not ingested within deadline")

        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT alarm_key FROM alarm_occurrences
                    WHERE rule_code='S6_DOOR_OPEN' AND device_id='DR-001'
                      AND payload_json->'event'->'data'->>'device_event_id'=%s""", (event_id,))
                occurrence = cur.fetchone()
        self.assertIsNotNone(occurrence, "S6 event did not create an alarm occurrence")
        self.alarm_keys.append(occurrence[0])

        # The separately running outbox-worker service delivers this row.
        deadline = time.monotonic() + 5
        delivery_state = ack_id = None
        while time.monotonic() < deadline:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT delivery_state, ack_id FROM alarm_outbox WHERE alarm_key=%s",
                                (occurrence[0],))
                    delivery_state, ack_id = cur.fetchone()
            if delivery_state == "DELIVERED":
                break
            time.sleep(0.1)
        self.assertEqual(delivery_state, "DELIVERED")
        self.assertTrue(bool(ack_id))


    def test_e2e_lwt_offline_and_reconnect(self):
        topic = "nw/v1/demo/demo-site/TH-001/status"
        offline_payload = json.dumps({"schema_version": 1, "device_id": "TH-001",
                                      "boot_id": self.boot_id, "ts": stamp(),
                                      "seq": 1, "state": "offline", "data": {}})
        online_payload = json.dumps({"schema_version": 1, "device_id": "TH-001",
                                     "boot_id": self.boot_id, "ts": stamp(),
                                     "seq": 2, "state": "online", "data": {}})

        # Connect with LWT set to offline
        publisher, reason = connected_client(
            self.th_user, self.th_pass, self.ca, f"lwt-{uuid.uuid4().hex[:6]}",
            will=(topic, offline_payload, 1, True))
        self.assertFalse(reason.is_failure)
        publisher.publish(topic, online_payload, qos=1, retain=True).wait_for_publish(5)

        # Abrupt disconnect -> triggers LWT
        publisher.loop_stop()
        publisher._sock_close()

        # Wait for Ingestion to record offline state
        deadline = time.monotonic() + 10
        offline_detected = False
        while time.monotonic() < deadline:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_state FROM device_status WHERE device_id='TH-001'")
                    row = cur.fetchone()
                    if row and row[0] == "offline":
                        offline_detected = True
                        break
            time.sleep(0.5)
        self.assertTrue(offline_detected, "LWT offline transition was not recorded within deadline")

        # Reconnect device online
        restore, reason = connected_client(self.th_user, self.th_pass, self.ca)
        self.assertFalse(reason.is_failure)
        restore.publish(topic, online_payload, qos=1, retain=True).wait_for_publish(5)
        restore.disconnect(); restore.loop_stop()

        # Wait for Ingestion to record online state
        deadline = time.monotonic() + 8
        online_restored = False
        while time.monotonic() < deadline:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_state FROM device_status WHERE device_id='TH-001'")
                    row = cur.fetchone()
                    if row and row[0] == "online":
                        online_restored = True
                        break
            time.sleep(0.25)
        self.assertTrue(online_restored, "Online status was not restored after reconnect")


if __name__ == "__main__":
    unittest.main()
