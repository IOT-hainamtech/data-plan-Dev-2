"""PostgreSQL integration tests; enable with RUN_DB_TESTS=1."""
import os
import unittest
from datetime import datetime, timedelta

from core.database import db_connection
from rules.engine import evaluate_due_rules, on_event, on_telemetry


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
        for s in (0, 60, 120, 180, 240, 300):
            on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                         measured_at=self.t0 + timedelta(seconds=s),
                         received_at=self.t0 + timedelta(seconds=s))
        with self.conn.cursor() as restarted:
            restarted.execute("SELECT is_active FROM rule_runtime_state WHERE rule_code='S1_TEMP_HIGH'")
            self.assertTrue(restarted.fetchone()[0])
        for s in (301, 361, 421):
            on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=28,
                         measured_at=self.t0 + timedelta(seconds=s), received_at=self.t0 + timedelta(seconds=s))
        self.cur.execute("SELECT occurrence_kind,count(*) FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH' GROUP BY occurrence_kind")
        self.assertEqual(dict(self.cur.fetchall()), {"TRIGGERED": 1, "CLEARED": 1})
        self.cur.execute("SELECT count(*) FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH')")
        self.assertEqual(self.cur.fetchone()[0], 2)
        self.cur.execute("""SELECT payload_json->>'kind',payload_json->'event'->'data'->>'summary'
            FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH' ORDER BY triggered_at""")
        payloads = self.cur.fetchall()
        self.assertEqual([row[0] for row in payloads], ["occurrence", "cleared"])
        self.assertTrue(all(row[1] for row in payloads))

    def test_s6_timer_duplicate_and_close(self):
        on_event(self.cur, device_id="DR-001", event_id="integration-open",
                 event_type="DOOR_OPENED", received_at=self.t0,
                 payload={"data": {"door_state": "open"}})
        evaluate_due_rules(self.cur, self.t0 + timedelta(seconds=300))
        evaluate_due_rules(self.cur, self.t0 + timedelta(seconds=400))
        on_event(self.cur, device_id="DR-001", event_id="integration-close",
                 event_type="DOOR_CLOSED", received_at=self.t0 + timedelta(seconds=401),
                 payload={"data": {"door_state": "closed"}})
        self.cur.execute("SELECT occurrence_kind,count(*) FROM alarm_occurrences WHERE rule_code='S6_DOOR_OPEN' GROUP BY occurrence_kind")
        self.assertEqual(dict(self.cur.fetchall()), {"TRIGGERED": 1, "CLEARED": 1})
        self.cur.execute("SELECT count(*) FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE rule_code='S6_DOOR_OPEN')")
        self.assertEqual(self.cur.fetchone()[0], 2)

    def test_duplicate_source_event_is_idempotent(self):
        for _ in range(2):
            on_event(self.cur, device_id="DR-001", event_id="same-event",
                     event_type="DOOR_OPENED", received_at=datetime(2026, 10, 14, 0, 0),
                     payload={"data": {"door_state": "open"}})
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE rule_code='S6_DOOR_OPEN' AND occurrence_kind='TRIGGERED'")
        self.assertEqual(self.cur.fetchone()[0], 1)
        self.cur.execute("SELECT count(*) FROM alarm_outbox WHERE alarm_key IN (SELECT alarm_key FROM alarm_occurrences WHERE rule_code='S6_DOOR_OPEN')")
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

    def test_s1_sample_gap_resets_continuity(self):
        # Sample at t0: 31°C
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                     measured_at=self.t0, received_at=self.t0)
        # Next sample at t0 + 200s (gap = 200s > 120s limit): continuity broken!
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                     measured_at=self.t0 + timedelta(seconds=200),
                     received_at=self.t0 + timedelta(seconds=200))
        # Verify NO alarm yet (should not trigger despite 200s elapsed since t0)
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH'")
        self.assertEqual(self.cur.fetchone()[0], 0)

        # Now send continuous samples every 60s from 200s to 500s (300s continuous from reset):
        for s in (260, 320, 380, 440, 500):
            on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                         measured_at=self.t0 + timedelta(seconds=s),
                         received_at=self.t0 + timedelta(seconds=s))
        # After 300s of continuous samples without gap > 120s: TRIGGERED!
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH' AND occurrence_kind='TRIGGERED'")
        self.assertEqual(self.cur.fetchone()[0], 1)

    def test_s1_quiet_window_suppression_and_new_episode(self):
        # 1. Trigger alarm at t0 + 300s
        for s in (0, 60, 120, 180, 240, 300):
            on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                         measured_at=self.t0 + timedelta(seconds=s),
                         received_at=self.t0 + timedelta(seconds=s))
        self.cur.execute("SELECT episode_key FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH' AND occurrence_kind='TRIGGERED'")
        ep1 = self.cur.fetchone()[0]

        # 2. Clear alarm at t0 + 421s (120s of temp < 29)
        for s in (301, 361, 421):
            on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=28,
                         measured_at=self.t0 + timedelta(seconds=s), received_at=self.t0 + timedelta(seconds=s))
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH'")
        self.assertEqual(self.cur.fetchone()[0], 2)  # 1 TRIGGERED, 1 CLEARED

        # 3. Temperature spikes again at t0 + 500s (within quiet_seconds=600s of t0 + 300s)
        # Continuous for 300s until t0 + 800s, must NOT fire another TRIGGERED because (800 - 300) = 500 < 600
        for s in (500, 560, 620, 680, 740, 800):
            on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                         measured_at=self.t0 + timedelta(seconds=s), received_at=self.t0 + timedelta(seconds=s))
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH' AND occurrence_kind='TRIGGERED'")
        self.assertEqual(self.cur.fetchone()[0], 1)  # Still only 1 TRIGGERED

        # 4. After quiet window expires (t0 + 300 + 601s = t0 + 901s):
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                     measured_at=self.t0 + timedelta(seconds=901), received_at=self.t0 + timedelta(seconds=901))
        self.cur.execute("SELECT episode_key FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH' AND occurrence_kind='TRIGGERED' ORDER BY triggered_at DESC")
        episodes = [row[0] for row in self.cur.fetchall()]
        self.assertEqual(len(episodes), 2)  # 2nd TRIGGERED fired!
        self.assertNotEqual(episodes[0], ep1)  # New episode generated!


if __name__ == "__main__":
    unittest.main()
