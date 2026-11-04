"""Automated integration tests for Device Online/Offline Status Tracker."""
import os
import unittest
import uuid
from datetime import datetime, timedelta
from unittest import mock

from core.database import db_connection
from ingestor import save_message
from status.service import (enqueue_status_reconciliation,
                            reconcile_device_timeouts, reconcile_fast_presence_timeouts,
                            update_device_presence, update_fast_presence)


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
                cur.execute("DELETE FROM status_outbox WHERE device_id=%s", (self.dev_id,))
                cur.execute("DELETE FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE device_id=%s)", (self.dev_id,))
                cur.execute("DELETE FROM alarm_occurrences WHERE device_id=%s", (self.dev_id,))
                cur.execute("DELETE FROM rule_runtime_state WHERE device_id=%s", (self.dev_id,))
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

    def test_fast_presence_order_timeout_and_transition_dedup(self):
        boot = "a1b2c3d4"
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=boot,
                                       received_at=self.t0, explicit_state="online", seq=0)
                accepted = update_fast_presence(cur, device_id=self.dev_id, boot_id=boot,
                                                seq=10, received_at=self.t0 + timedelta(seconds=1))
                duplicate = update_fast_presence(cur, device_id=self.dev_id, boot_id=boot,
                                                 seq=10, received_at=self.t0 + timedelta(seconds=2))
                old_sequence = update_fast_presence(cur, device_id=self.dev_id, boot_id=boot,
                                                    seq=9, received_at=self.t0 + timedelta(seconds=3))
                old_boot = update_fast_presence(cur, device_id=self.dev_id, boot_id="deadbeef",
                                                seq=99, received_at=self.t0 + timedelta(seconds=3))
            conn.commit()
        self.assertEqual((accepted, duplicate, old_sequence, old_boot), (1, 0, 0, 0))
        with db_connection() as conn:
            with conn.cursor() as cur:
                # Missing one or two 1-second heartbeats must remain online.
                self.assertEqual(reconcile_fast_presence_timeouts(
                    cur, self.t0 + timedelta(seconds=3.9), [self.dev_id], 4), 0)
                self.assertEqual(reconcile_fast_presence_timeouts(
                    cur, self.t0 + timedelta(seconds=5), [self.dev_id], 4), 1)
                # Repeated reconciliation cannot duplicate the transition.
                self.assertEqual(reconcile_fast_presence_timeouts(
                    cur, self.t0 + timedelta(seconds=6), [self.dev_id], 4), 0)
            conn.commit()
        self.assertEqual(self.get_status()[0], "offline")
        self.assertEqual(self.get_history_count("offline"), 1)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT count(*),bool_and(payload_json->>'transition'='true')
                    FROM status_outbox WHERE device_id=%s""", (self.dev_id,))
                # One birth and one real offline transition; heartbeat rows are absent.
                self.assertEqual(cur.fetchone(), (2, True))

    def test_birth_clears_stale_fast_presence_deadline(self):
        boot = "a1b2c3d4"
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=boot,
                                       received_at=self.t0, explicit_state="online")
                update_fast_presence(cur, device_id=self.dev_id, boot_id=boot, seq=5,
                                     received_at=self.t0 + timedelta(seconds=1))
                # Retained/reconnect birth must disarm the old deadline until
                # the new live presence stream is observed.
                update_device_presence(cur, device_id=self.dev_id, boot_id=boot,
                                       received_at=self.t0 + timedelta(seconds=10),
                                       explicit_state="online", seq=10)
                self.assertEqual(update_fast_presence(
                    cur, device_id=self.dev_id, boot_id=boot, seq=5,
                    received_at=self.t0 + timedelta(seconds=11)), 0)
                self.assertEqual(reconcile_fast_presence_timeouts(
                    cur, self.t0 + timedelta(seconds=20), [self.dev_id], 4), 0)
                cur.execute("SELECT presence_received_at FROM device_status WHERE device_id=%s",
                            (self.dev_id,))
                self.assertIsNone(cur.fetchone()[0])
            conn.commit()

    def test_legacy_device_without_presence_keeps_120_second_timeout(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=self.boot_id,
                                       received_at=self.t0, explicit_state="online")
                self.assertEqual(reconcile_fast_presence_timeouts(
                    cur, self.t0 + timedelta(seconds=30), [self.dev_id], 4), 0)
                self.assertEqual(reconcile_device_timeouts(
                    cur, self.t0 + timedelta(seconds=119), device_id=self.dev_id), 0)
                self.assertEqual(reconcile_device_timeouts(
                    cur, self.t0 + timedelta(seconds=121), device_id=self.dev_id), 1)
            conn.commit()
        self.assertEqual(self.get_status()[0], "offline")
        # Running legacy reconciliation again does not duplicate the transition.
        with db_connection() as conn:
            with conn.cursor() as cur:
                count = reconcile_device_timeouts(
                    cur, self.t0 + timedelta(seconds=130), device_id=self.dev_id)
            conn.commit()
        self.assertEqual(count, 0)
        self.assertEqual(self.get_history_count("offline"), 1)

    def test_presence_ingestion_is_opt_in_and_never_creates_business_data(self):
        boot = "a1b2c3d4"
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=boot,
                                       received_at=self.t0, explicit_state="online")
            conn.commit()
        payload = {"schema_version": 1, "msg_kind": "presence", "device_id": self.dev_id,
                   "boot_id": boot, "seq": 10, "ts": "2026-11-03T08:30:00Z",
                   "ts_source": "ntp", "state": "online", "reason": "heartbeat", "data": {}}
        topic = f"nw/v1/demo/demo-site/{self.dev_id}/status"
        with mock.patch.dict(os.environ, {"FAST_PRESENCE_DEVICE_IDS": ""}):
            self.assertEqual(save_message(topic, payload), 0)
        with mock.patch.dict(os.environ, {"FAST_PRESENCE_DEVICE_IDS": self.dev_id}), \
             mock.patch("ingestor.on_telemetry") as telemetry_rule, \
             mock.patch("ingestor.on_event") as event_rule:
            self.assertEqual(save_message(topic, payload), 1)
            telemetry_rule.assert_not_called()
            event_rule.assert_not_called()
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM telemetry_reports WHERE device_id=%s", (self.dev_id,))
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT count(*) FROM device_state_snapshots WHERE device_id=%s", (self.dev_id,))
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("SELECT count(*) FROM alarm_occurrences WHERE device_id=%s", (self.dev_id,))
                self.assertEqual(cur.fetchone()[0], 0)

    def test_periodic_reconciliation_is_idempotent_per_five_minute_bucket(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                update_device_presence(cur, device_id=self.dev_id, boot_id=self.boot_id,
                                       received_at=self.t0, explicit_state="online")
                first = enqueue_status_reconciliation(cur, self.t0)
                second = enqueue_status_reconciliation(cur, self.t0 + timedelta(minutes=4))
            conn.commit()
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT count(*) FROM status_outbox
                    WHERE device_id=%s AND payload_json->>'reason'='telemetry'""",
                    (self.dev_id,))
                # The immediate online change uses reason=connect; exactly one
                # periodic snapshot in this bucket uses reason=telemetry.
                self.assertEqual(cur.fetchone()[0], 1)

if __name__ == "__main__":
    unittest.main()
