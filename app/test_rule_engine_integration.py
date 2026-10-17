"""PostgreSQL integration tests; enable with RUN_DB_TESTS=1."""
import os
import unittest
from datetime import datetime, timedelta

from ingestor import db_connection
from rule_engine import evaluate_due_rules, on_event, on_telemetry


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class RuleDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = db_connection()
        self.cur = self.conn.cursor()
        self.cur.execute("DELETE FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE rule_code IN ('S1_TEMP_HIGH','S6_DOOR_OPEN'))")
        self.cur.execute("DELETE FROM alarm_occurrences WHERE rule_code IN ('S1_TEMP_HIGH','S6_DOOR_OPEN')")
        self.cur.execute("DELETE FROM rule_runtime_state WHERE rule_code IN ('S1_TEMP_HIGH','S6_DOOR_OPEN')")
        self.t0 = datetime(2026, 10, 14, 2, 0)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_s1_trigger_clear_outbox_and_restart_state(self):
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                     measured_at=self.t0, received_at=self.t0)
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                     measured_at=self.t0 + timedelta(seconds=300),
                     received_at=self.t0 + timedelta(seconds=300))
        with self.conn.cursor() as restarted:
            restarted.execute("SELECT is_active FROM rule_runtime_state WHERE rule_code='S1_TEMP_HIGH'")
            self.assertTrue(restarted.fetchone()[0])
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=28,
                     measured_at=self.t0 + timedelta(seconds=301), received_at=self.t0 + timedelta(seconds=301))
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=28,
                     measured_at=self.t0 + timedelta(seconds=421), received_at=self.t0 + timedelta(seconds=421))
        self.cur.execute("SELECT occurrence_kind,count(*) FROM alarm_occurrences GROUP BY occurrence_kind")
        self.assertEqual(dict(self.cur.fetchall()), {"TRIGGERED": 1, "CLEARED": 1})
        self.cur.execute("SELECT count(*) FROM alarm_outbox")
        self.assertEqual(self.cur.fetchone()[0], 2)

    def test_s6_timer_duplicate_and_close(self):
        on_event(self.cur, device_id="DR-001", event_id="integration-open",
                 event_type="DOOR_OPENED", received_at=self.t0,
                 payload={"data": {"door_state": "open"}})
        evaluate_due_rules(self.cur, self.t0 + timedelta(seconds=300))
        evaluate_due_rules(self.cur, self.t0 + timedelta(seconds=400))
        on_event(self.cur, device_id="DR-001", event_id="integration-close",
                 event_type="DOOR_CLOSED", received_at=self.t0 + timedelta(seconds=401),
                 payload={"data": {"door_state": "closed"}})
        self.cur.execute("SELECT occurrence_kind,count(*) FROM alarm_occurrences GROUP BY occurrence_kind")
        self.assertEqual(dict(self.cur.fetchall()), {"TRIGGERED": 1, "CLEARED": 1})
        self.cur.execute("SELECT count(*) FROM alarm_outbox")
        self.assertEqual(self.cur.fetchone()[0], 2)

    def test_duplicate_source_event_is_idempotent(self):
        for _ in range(2):
            on_event(self.cur, device_id="DR-001", event_id="same-event",
                     event_type="DOOR_OPENED", received_at=datetime(2026, 10, 14, 0, 0),
                     payload={"data": {"door_state": "open"}})
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE occurrence_kind='TRIGGERED'")
        self.assertEqual(self.cur.fetchone()[0], 1)
        self.cur.execute("SELECT count(*) FROM alarm_outbox")
        self.assertEqual(self.cur.fetchone()[0], 1)

    def test_heartbeat_reconciles_missing_open_and_close_events(self):
        on_event(self.cur, device_id="DR-001", event_id="heartbeat:boot-a:10",
                 event_type="DOOR_OPENED", received_at=self.t0,
                 observed_at=self.t0, payload={"data": {"door_state": "open"}})
        evaluate_due_rules(self.cur, self.t0 + timedelta(seconds=300))
        on_event(self.cur, device_id="DR-001", event_id="heartbeat:boot-a:11",
                 event_type="DOOR_CLOSED", received_at=self.t0 + timedelta(seconds=360),
                 observed_at=self.t0 + timedelta(seconds=360),
                 payload={"data": {"door_state": "closed"}})
        self.cur.execute("""SELECT occurrence_kind,count(*) FROM alarm_occurrences
            WHERE episode_key LIKE 'ep:S6_DOOR_OPEN:%' GROUP BY occurrence_kind""")
        self.assertEqual(dict(self.cur.fetchall()), {"TRIGGERED": 1, "CLEARED": 1})

    def test_stale_door_observation_cannot_reverse_newer_heartbeat(self):
        on_event(self.cur, device_id="DR-001", event_id="heartbeat:boot-b:20",
                 event_type="DOOR_OPENED", received_at=self.t0,
                 observed_at=self.t0, payload={"data": {"door_state": "open"}})
        on_event(self.cur, device_id="DR-001", event_id="heartbeat:boot-b:22",
                 event_type="DOOR_CLOSED", received_at=self.t0 + timedelta(seconds=20),
                 observed_at=self.t0 + timedelta(seconds=20),
                 payload={"data": {"door_state": "closed"}})
        on_event(self.cur, device_id="DR-001", event_id="heartbeat:boot-b:21",
                 event_type="DOOR_OPENED", received_at=self.t0 + timedelta(seconds=30),
                 observed_at=self.t0 + timedelta(seconds=10),
                 payload={"data": {"door_state": "open"}})
        self.cur.execute("""SELECT current_state FROM rule_runtime_state
            WHERE rule_code='S6_DOOR_OPEN' AND device_id='DR-001'""")
        self.assertEqual(self.cur.fetchone()[0], "closed")


if __name__ == "__main__":
    unittest.main()
