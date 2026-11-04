"""Firmware contract plus optional PostgreSQL integration tests."""
import os
from types import SimpleNamespace
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock

from core.database import db_connection
from ingestion.contracts import (door_heartbeat_event, validate_door_heartbeat, validate_message,
                                 validate_telemetry_data)
from ingestor import (BUSINESS_EXECUTOR, PRESENCE_EXECUTOR, DeviceRateLimiter,
                      on_message, rate_limit_key, save_message)


class IngestorContractTests(unittest.TestCase):
    def test_presence_dispatch_is_isolated_from_business_processing(self):
        presence = SimpleNamespace(topic="nw/v1/hainam/hq/TH-001/status",
            payload=b'{"msg_kind":"presence"}')
        telemetry = SimpleNamespace(topic="nw/v1/hainam/hq/TH-001/telemetry",
            payload=b'{"msg_kind":"telemetry"}')
        with mock.patch.object(PRESENCE_EXECUTOR, "submit") as presence_submit, \
             mock.patch.object(BUSINESS_EXECUTOR, "submit") as business_submit:
            on_message(None, None, presence)
            on_message(None, None, telemetry)
        presence_submit.assert_called_once()
        business_submit.assert_called_once()

    def test_presence_uses_an_independent_rate_limit_bucket(self):
        self.assertEqual(rate_limit_key("TH-001", {"msg_kind": "presence"}),
                         "TH-001:presence")
        self.assertEqual(rate_limit_key("TH-001", {"msg_kind": "telemetry"}), "TH-001")
        self.assertEqual(rate_limit_key("TH-001", {"msg_kind": "event"}), "TH-001")
        with mock.patch("ingestor.time.monotonic",
                        side_effect=[0.0, 0.0, 0.2, 0.0, 0.0, 0.2]):
            business = DeviceRateLimiter(capacity=1, refill_per_second=1)
            presence = DeviceRateLimiter(capacity=1, refill_per_second=5)
            self.assertTrue(business.allow("TH-001"))
            self.assertFalse(business.allow("TH-001"))
            self.assertTrue(presence.allow("TH-001:presence"))
            self.assertTrue(presence.allow("TH-001:presence"))

    def test_one_hertz_business_traffic_has_clock_jitter_margin(self):
        # 0.96-second arrivals are 1.04 Hz: valid for a nominal one-second
        # sensor schedule, and must not slowly drain the burst bucket.
        limiter = DeviceRateLimiter(capacity=5, refill_per_second=1.25)
        timeline = [index * 0.96 for index in range(120)]
        # The first allow() also initializes the bucket timestamp.
        clock_values = [timeline[0], timeline[0], *timeline[1:]]
        with mock.patch("ingestor.time.monotonic", side_effect=clock_values):
            self.assertTrue(all(limiter.allow("TH-001") for _ in timeline))

    def test_sht31_error_is_telemetry_without_fake_measurements(self):
        quality, metrics = validate_telemetry_data({"quality": "error", "data": {}})
        self.assertEqual(quality, "error")
        self.assertEqual(metrics, {})
        with self.assertRaisesRegex(ValueError, "must not contain measurements"):
            validate_telemetry_data({"quality": "error", "data": {"temperature": 0}})

    def test_valid_sht31_values_accept_explicit_quality(self):
        quality, metrics = validate_telemetry_data({
            "quality": "good", "data": {"temperature": 29.5, "humidity": 61.0}})
        self.assertEqual(quality, "good")
        self.assertEqual(set(metrics), {"temperature", "humidity"})
        with self.assertRaisesRegex(ValueError, "good or error"):
            validate_telemetry_data({
                "quality": "ok", "data": {"temperature": 29.5, "humidity": 61.0}})

    def test_good_is_default_and_quality_inside_data_is_rejected(self):
        quality, _ = validate_telemetry_data(
            {"data": {"temperature": 29.5, "humidity": 61.0}})
        self.assertEqual(quality, "good")
        with self.assertRaisesRegex(ValueError, "unsupported telemetry"):
            validate_telemetry_data({
                "data": {"quality": "good", "temperature": 29.5, "humidity": 61.0}})

    def test_door_snapshot_maps_to_idempotent_rule_observation_not_transition_id(self):
        body = {"boot_id": "boot-a", "seq": 42, "data": {"door_state": "open"}}
        self.assertEqual(door_heartbeat_event("DOOR_SENSOR", body),
                         ("heartbeat:boot-a:42", "DOOR_OPENED"))
        body["data"]["door_state"] = "closed"
        self.assertEqual(door_heartbeat_event("DOOR_SENSOR", body),
                         ("heartbeat:boot-a:42", "DOOR_CLOSED"))

    def test_base_contract_rejects_bad_device_and_sequence(self):
        body = {"schema_version": 1, "device_id": "DR-001", "boot_id": "boot",
                "seq": 1, "ts": "2026-10-17T00:00:00Z", "state": "online",
                "data": {"door_state": "closed"}}
        validate_message("nw/v1/demo/demo-site/DR-001/status", body)
        body["device_id"] = "TH-001"
        with self.assertRaisesRegex(ValueError, "mismatch"):
            validate_message("nw/v1/demo/demo-site/DR-001/status", body)

    def test_v11_message_kind_boot_id_and_door_heartbeat(self):
        body = {"schema_version": 1, "msg_kind": "heartbeat", "device_id": "DR-001",
                "boot_id": "a1b2c3d4", "seq": 9, "ts": "2026-10-30T03:30:00Z",
                "ts_source": "ntp", "quality": "good",
                "data": {"door_state": "closed"}}
        result = validate_message("nw/v1/demo/demo-site/DR-001/telemetry", body)
        self.assertEqual(result[3], "telemetry")
        self.assertEqual(validate_door_heartbeat(body, {"door_state": {
            "data_type": "string", "allowed_values": ["open", "closed"]}}), "closed")
        body["boot_id"] = "legacy-boot-id"
        with self.assertRaisesRegex(ValueError, "6-8 hexadecimal"):
            validate_message("nw/v1/demo/demo-site/DR-001/telemetry", body)
        body["boot_id"] = "a1b2c3d4"
        body["msg_kind"] = "event"
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_message("nw/v1/demo/demo-site/DR-001/telemetry", body)
        body["device_id"] = "DR-001"
        body["seq"] = -1
        with self.assertRaisesRegex(ValueError, "non-negative"):
            validate_message("nw/v1/demo/demo-site/DR-001/status", body)

    def test_timestamp_source_none_preserves_provenance(self):
        body = {"schema_version": 1, "msg_kind": "heartbeat", "device_id": "DR-001",
                "boot_id": "a1b2c3d4", "seq": 10, "ts_source": "none",
                "quality": "good", "data": {"door_state": "closed"}}
        result = validate_message("nw/v1/demo/demo-site/DR-001/telemetry", body)
        self.assertEqual(result[5], "none")

    def test_fast_presence_contract_is_status_only_and_empty(self):
        body = {"schema_version": 1, "msg_kind": "presence", "device_id": "TH-001",
                "boot_id": "a1b2c3d4", "seq": 12, "ts": "2026-11-03T08:30:00Z",
                "ts_source": "ntp", "state": "online", "reason": "heartbeat", "data": {}}
        result = validate_message("nw/v1/hainam/hq/TH-001/status", body)
        self.assertEqual(result[3], "status")
        for field, value, message in (("state", "offline", "online heartbeat"),
                                      ("reason", "connect", "online heartbeat")):
            invalid = dict(body); invalid[field] = value
            with self.assertRaisesRegex(ValueError, message):
                validate_message("nw/v1/hainam/hq/TH-001/status", invalid)
        invalid = dict(body); invalid["data"] = {"temperature": 20}
        with self.assertRaisesRegex(ValueError, "data must be empty"):
            validate_message("nw/v1/hainam/hq/TH-001/status", invalid)


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class TelemetryQualityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.boot_id = f"quality-test-{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM telemetry WHERE device_id='TH-001' AND boot_id=%s",
                            (self.boot_id,))
                cur.execute("DELETE FROM telemetry_reports WHERE device_id='TH-001' AND boot_id=%s",
                            (self.boot_id,))
                cur.execute("DELETE FROM device_event_outbox WHERE event_id LIKE %s",
                            (f"TH-001-{self.boot_id}-%",))
            conn.commit()

    def test_error_report_is_persisted_without_numeric_measurements(self):
        body = {"schema_version": 1, "device_id": "TH-001", "boot_id": self.boot_id,
                "seq": 1, "ts": "2026-10-17T00:00:00Z",
                "quality": "error", "data": {}}
        with mock.patch("ingestor.on_telemetry") as evaluate_rule:
            save_message("nw/v1/demo/demo-site/TH-001/telemetry", body)
            evaluate_rule.assert_not_called()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT quality FROM telemetry_reports
                    WHERE device_id='TH-001' AND boot_id=%s AND seq=1""", (self.boot_id,))
                self.assertEqual(cur.fetchone()[0], "error")
                cur.execute("SELECT count(*) FROM telemetry WHERE boot_id=%s", (self.boot_id,))
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT boot_id FROM device_status WHERE device_id='TH-001'")
                self.assertEqual(cur.fetchone()[0], self.boot_id)

    def test_missing_and_skewed_timestamp_are_stored_but_not_evaluated(self):
        cases = [
            {"seq": 10, "ts_source": "none"},
            {"seq": 11, "ts": "2020-01-01T00:00:00Z", "ts_source": "ntp"},
        ]
        for case in cases:
            body = {"schema_version": 1, "device_id": "TH-001", "boot_id": self.boot_id,
                    "quality": "good", "data": {"temperature": 31.0, "humidity": 60.0},
                    **case}
            with mock.patch("ingestor.on_telemetry") as evaluate_rule:
                save_message("nw/v1/demo/demo-site/TH-001/telemetry", body)
                evaluate_rule.assert_not_called()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT seq,ts_source,ts_suspect FROM telemetry_reports
                    WHERE device_id='TH-001' AND boot_id=%s AND seq IN (10,11) ORDER BY seq""",
                    (self.boot_id,))
                rows = cur.fetchall()
                self.assertEqual([(row[0], row[1], row[2]) for row in rows],
                                 [(10, "none", True), (11, "ntp", True)])
                cur.execute("SELECT count(*) FROM telemetry WHERE boot_id=%s AND seq IN (10,11)",
                            (self.boot_id,))
                self.assertEqual(cur.fetchone()[0], 4)
                cur.execute("SELECT boot_id FROM device_status WHERE device_id='TH-001'")
                self.assertEqual(cur.fetchone()[0], self.boot_id)

    def test_duplicate_boot_sequence_is_idempotent(self):
        body = {"schema_version": 1, "device_id": "TH-001", "boot_id": self.boot_id,
                "seq": 2, "ts": "2026-10-17T00:01:00Z",
                "quality": "good", "data": {"temperature": 28.0, "humidity": 60.0}}
        save_message("nw/v1/demo/demo-site/TH-001/telemetry", body)
        save_message("nw/v1/demo/demo-site/TH-001/telemetry", body)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM telemetry_reports WHERE boot_id=%s",
                            (self.boot_id,))
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT count(*) FROM telemetry WHERE boot_id=%s", (self.boot_id,))
                self.assertEqual(cur.fetchone()[0], 2)

    def test_valid_live_telemetry_creates_one_deterministic_outbound_event(self):
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        body = {"schema_version": 1, "device_id": "TH-001", "boot_id": self.boot_id,
                "seq": 20, "ts": stamp, "ts_source": "ntp", "quality": "good",
                "data": {"temperature": 28.0, "humidity": 60.0}}
        save_message("nw/v1/demo/demo-site/TH-001/telemetry", body)
        save_message("nw/v1/demo/demo-site/TH-001/telemetry", body)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT event_id,payload_json->>'event_type',
                    jsonb_array_length(payload_json->'metrics')
                    FROM device_event_outbox WHERE event_id=%s""",
                    (f"TH-001-{self.boot_id}-20-telemetry",))
                self.assertEqual(cur.fetchone(),
                    (f"TH-001-{self.boot_id}-20-telemetry", "TELEMETRY_UPDATED", 2))


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class DoorSnapshotDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.boot_id = uuid.uuid4().hex[:8]

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM device_state_snapshots WHERE device_id='DR-001' AND boot_id=%s",
                            (self.boot_id,))
                cur.execute("DELETE FROM device_events WHERE device_id='DR-001' AND boot_id=%s",
                            (self.boot_id,))
                cur.execute("DELETE FROM device_event_outbox WHERE event_id LIKE %s",
                            (f"DR-001-{self.boot_id}-%",))
                cur.execute("DELETE FROM rule_runtime_state WHERE device_id='DR-001'")
            conn.commit()

    def test_real_door_transition_creates_one_outbound_event_not_heartbeat(self):
        event_id = f"DR-001-{self.boot_id}-100"
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        body = {"schema_version": 1, "msg_kind": "event", "device_id": "DR-001",
                "boot_id": self.boot_id, "seq": 100, "ts": stamp, "ts_source": "ntp",
                "event_id": event_id, "event_type": "DOOR_OPENED",
                "data": {"door_state": "open"}}
        save_message("nw/v1/demo/demo-site/DR-001/event", body)
        save_message("nw/v1/demo/demo-site/DR-001/event", body)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT count(*),min(payload_json->>'door_state')
                    FROM device_event_outbox WHERE event_id=%s""", (event_id,))
                self.assertEqual(cur.fetchone(), (1, "open"))

    def test_telemetry_heartbeat_is_persisted_without_fake_device_event(self):
        body = {"schema_version": 1, "msg_kind": "heartbeat", "device_id": "DR-001",
                "boot_id": self.boot_id, "seq": 101, "ts": "2026-10-30T03:30:00Z",
                "ts_source": "ntp", "quality": "good",
                "data": {"door_state": "closed"}}
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM device_events WHERE device_id='DR-001'")
                before = cur.fetchone()[0]
        save_message("nw/v1/demo/demo-site/DR-001/telemetry", body)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT seq,state_json->>'door_state',measured_at,ts_source
                    FROM device_state_snapshots WHERE device_id='DR-001' AND boot_id=%s""",
                    (self.boot_id,))
                self.assertEqual(cur.fetchone(), (101, "closed", mock.ANY, "ntp"))
                cur.execute("SELECT count(*) FROM device_events WHERE device_id='DR-001'")
                self.assertEqual(cur.fetchone()[0], before)
                cur.execute("SELECT count(*) FROM device_event_outbox WHERE event_id LIKE %s",
                            (f"DR-001-{self.boot_id}-%",))
                self.assertEqual(cur.fetchone()[0], 0)
        from api.application import latest
        projection = latest("DR-001", "demo")
        self.assertEqual(projection["door_state"], "closed")
        self.assertEqual(projection["latest_snapshot"]["seq"], 101)
        self.assertEqual(projection["latest_snapshot"]["measured_at_local"],
                         "2026-10-30T10:30:00+07:00")

    def test_suspect_heartbeat_is_stored_but_never_reaches_rule_engine(self):
        body = {"schema_version": 1, "msg_kind": "heartbeat", "device_id": "DR-001",
                "boot_id": self.boot_id, "seq": 102, "ts": "2020-01-01T00:00:00Z",
                "ts_source": "ntp", "quality": "good", "data": {"door_state": "open"}}
        with mock.patch("ingestor.on_event") as evaluate_rule:
            save_message("nw/v1/demo/demo-site/DR-001/telemetry", body)
            evaluate_rule.assert_not_called()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT ts_suspect FROM device_state_snapshots
                    WHERE device_id='DR-001' AND boot_id=%s AND seq=102""", (self.boot_id,))
                self.assertTrue(cur.fetchone()[0])

    def test_suspect_transition_event_updates_presence_but_not_rule_state(self):
        event_id = f"DR-001-{self.boot_id}-103"
        body = {"schema_version": 1, "msg_kind": "event", "device_id": "DR-001",
                "boot_id": self.boot_id, "seq": 103, "ts": "2035-01-01T00:00:00Z",
                "ts_source": "ntp", "event_id": event_id, "event_type": "DOOR_OPENED",
                "data": {"door_state": "open"}}
        try:
            with mock.patch("ingestor.on_event") as evaluate_rule:
                save_message("nw/v1/demo/demo-site/DR-001/event", body)
                evaluate_rule.assert_not_called()
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT ts_suspect FROM device_events WHERE device_id='DR-001' AND event_id=%s", (event_id,))
                    self.assertTrue(cur.fetchone()[0])
                    cur.execute("SELECT current_state FROM device_status WHERE device_id='DR-001'")
                    self.assertIn(cur.fetchone()[0], ("online", "offline"))
        finally:
            with db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM device_events WHERE device_id='DR-001' AND event_id=%s", (event_id,))
                conn.commit()

    def test_suspect_status_snapshot_is_stored_but_not_evaluated(self):
        body = {"schema_version": 1, "msg_kind": "status", "device_id": "DR-001",
                "boot_id": self.boot_id, "seq": 104, "ts": "2020-01-01T00:00:00Z",
                "ts_source": "ntp", "state": "online", "data": {"door_state": "closed"}}
        with mock.patch("ingestor.on_event") as evaluate_rule:
            save_message("nw/v1/demo/demo-site/DR-001/status", body)
            evaluate_rule.assert_not_called()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT ts_suspect FROM device_state_snapshots
                    WHERE device_id='DR-001' AND boot_id=%s AND seq=104""", (self.boot_id,))
                self.assertTrue(cur.fetchone()[0])


if __name__ == "__main__":
    unittest.main()
