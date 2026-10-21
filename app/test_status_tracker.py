"""Automated integration tests for Device Online/Offline Status Tracker."""
import os
import unittest
import uuid
from datetime import datetime, timedelta

from ingestor import db_connection, reconcile_device_timeouts, save_message, update_device_presence


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class StatusTrackerTests(unittest.TestCase):
    def setUp(self):
        self.dev_id = f"TEST-DEV-{uuid.uuid4().hex[:8]}"
        self.boot_id = f"boot-{uuid.uuid4().hex[:6]}"
        self.t0 = datetime(2026, 10, 17, 8, 0, 0)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO devices (device_id, org_code, site_code, profile_code, enabled)
                    VALUES (%s, 'demo', 'demo-site', 'TEMP_HUM_SENSOR', TRUE)""", (self.dev_id,))
            conn.commit()

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM device_status_history WHERE device_id=%s", (self.dev_id,))
                cur.execute("DELETE FROM device_status WHERE device_id=%s", (self.dev_id,))
                cur.execute("DELETE FROM telemetry WHERE device_id=%s", (self.dev_id,))
                cur.execute("DELETE FROM telemetry_reports WHERE device_id=%s", (self.dev_id,))
                cur.execute("DELETE FROM devices WHERE device_id=%s", (self.dev_id,))
            conn.commit()

    def get_status(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_state, last_seen_at, timeout_seconds FROM device_status WHERE device_id=%s",
                            (self.dev_id,))
                return cur.fetchone()

    def get_history_count(self, state=None):
        with db_connection() as conn:
            with conn.cursor() as cur:
                if state:
                    cur.execute("SELECT count(*) FROM device_status_history WHERE device_id=%s AND state=%s",
                                (self.dev_id, state))
                else:
                    cur.execute("SELECT count(*) FROM device_status_history WHERE device_id=%s", (self.dev_id,))
                return cur.fetchone()[0]

    def test_birth_online_marks_device_online_and_records_history(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                inserted = update_device_presence(cur, device_id=self.dev_id, boot_id=self.boot_id,
                                                  received_at=self.t0, explicit_state="online")
            conn.commit()
        self.assertEqual(inserted, 1)
        status = self.get_status()
        self.assertEqual(status[0], "online")
        self.assertEqual(status[1], self.t0)
        self.assertEqual(self.get_history_count("online"), 1)

    def test_lwt_offline_marks_device_offline_and_records_history(self):
        # 1. First online
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=self.boot_id,
                                       received_at=self.t0, explicit_state="online")
            conn.commit()
        # 2. LWT arrives
        t1 = self.t0 + timedelta(seconds=10)
        with db_connection() as conn:
            with conn.cursor() as cur:
                inserted = update_device_presence(cur, device_id=self.dev_id, boot_id=self.boot_id,
                                                  received_at=t1, explicit_state="offline")
            conn.commit()
        self.assertEqual(inserted, 1)
        status = self.get_status()
        self.assertEqual(status[0], "offline")
        self.assertEqual(status[1], self.t0)  # last_seen_at preserved from last online
        self.assertEqual(self.get_history_count("offline"), 1)

    def test_telemetry_refreshes_last_seen_without_duplicate_history(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=self.boot_id,
                                       received_at=self.t0, explicit_state="online")
            conn.commit()
        self.assertEqual(self.get_history_count("online"), 1)

        # Telemetry arrives 60s later
        t1 = self.t0 + timedelta(seconds=60)
        topic = f"nw/v1/demo/demo-site/{self.dev_id}/telemetry"
        payload = {
            "schema_version": 1, "device_id": self.dev_id, "boot_id": self.boot_id,
            "seq": 1, "ts": t1.isoformat() + "Z", "quality": "good",
            "data": {"temperature": 25.5, "humidity": 50.0}
        }
        save_message(topic, payload)

        status = self.get_status()
        self.assertEqual(status[0], "online")
        # No extra history entry created while staying online
        self.assertEqual(self.get_history_count("online"), 1)

    def test_offline_device_reconnects_on_valid_telemetry(self):
        # Device starts offline
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=self.boot_id,
                                       received_at=self.t0, explicit_state="offline")
            conn.commit()
        self.assertEqual(self.get_status()[0], "offline")

        # Telemetry arrives from reconnect
        t1 = self.t0 + timedelta(seconds=30)
        topic = f"nw/v1/demo/demo-site/{self.dev_id}/telemetry"
        payload = {
            "schema_version": 1, "device_id": self.dev_id, "boot_id": self.boot_id,
            "seq": 1, "ts": t1.isoformat() + "Z", "quality": "good",
            "data": {"temperature": 26.0, "humidity": 55.0}
        }
        save_message(topic, payload)

        status = self.get_status()
        self.assertEqual(status[0], "online")
        self.assertEqual(self.get_history_count("online"), 1)

    def test_timeout_reconciler_marks_idle_device_offline(self):
        # Set up device online with timeout_seconds=120
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=self.boot_id,
                                       received_at=self.t0, explicit_state="online")
                cur.execute("UPDATE device_status SET timeout_seconds=120 WHERE device_id=%s", (self.dev_id,))
            conn.commit()

        # At t0 + 100s: not timed out yet
        with db_connection() as conn:
            with conn.cursor() as cur:
                count = reconcile_device_timeouts(cur, self.t0 + timedelta(seconds=100), device_id=self.dev_id)
            conn.commit()
        self.assertEqual(count, 0)
        self.assertEqual(self.get_status()[0], "online")

        # At t0 + 121s: exceeds 120s timeout -> marked offline
        with db_connection() as conn:
            with conn.cursor() as cur:
                count = reconcile_device_timeouts(cur, self.t0 + timedelta(seconds=121), device_id=self.dev_id)
            conn.commit()
        self.assertEqual(count, 1)
        status = self.get_status()
        self.assertEqual(status[0], "offline")
        self.assertEqual(self.get_history_count("offline"), 1)

        # Running reconciler again at t0 + 130s does NOT duplicate offline transition
        with db_connection() as conn:
            with conn.cursor() as cur:
                count = reconcile_device_timeouts(cur, self.t0 + timedelta(seconds=130), device_id=self.dev_id)
            conn.commit()
        self.assertEqual(count, 0)
        self.assertEqual(self.get_history_count("offline"), 1)


if __name__ == "__main__":
    unittest.main()
