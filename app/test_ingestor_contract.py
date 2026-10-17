"""Unit tests for the firmware/Data Plane message contract."""
import os
import unittest
import uuid
from unittest import mock

from ingestor import (db_connection, door_heartbeat_event, save_message,
                      validate_message, validate_telemetry_data)


class IngestorContractTests(unittest.TestCase):
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

    def test_door_heartbeat_maps_to_idempotent_observation(self):
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
        body["device_id"] = "DR-001"
        body["seq"] = -1
        with self.assertRaisesRegex(ValueError, "non-negative"):
            validate_message("nw/v1/demo/demo-site/DR-001/status", body)


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


if __name__ == "__main__":
    unittest.main()
