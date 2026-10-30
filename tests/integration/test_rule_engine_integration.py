"""PostgreSQL integration tests; enable with RUN_DB_TESTS=1."""
import os
import unittest
import uuid
from datetime import datetime, timedelta

from api.integration import alarm_state
from api.models import AlarmStateIn
from core.database import db_connection
from rules.engine import (_manually_closed_uncleared_episode, evaluate_due_rules,
                          on_event, on_telemetry)


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
        self.cur.execute("""SELECT event_type FROM alarm_occurrences
            WHERE rule_code='S6_DOOR_OPEN' AND occurrence_kind='TRIGGERED'""")
        self.assertEqual(self.cur.fetchone()[0], "DOOR_OPEN_TOO_LONG")

    def test_s6_outside_business_hours_fires_immediately_with_correct_event_type(self):
        outside = datetime(2026, 10, 14, 0, 30)  # 07:30 Asia/Ho_Chi_Minh
        on_event(self.cur, device_id="DR-001", event_id="outside-open",
                 event_type="DOOR_OPENED", received_at=outside,
                 observed_at=outside, payload={"data": {"door_state": "open"}})
        self.cur.execute("""SELECT event_type,payload_json->'event'->>'event_type'
            FROM alarm_occurrences WHERE rule_code='S6_DOOR_OPEN'
              AND occurrence_kind='TRIGGERED'""")
        # Occurrence classification is the Rule result; nested event_type stays
        # the source device transition expected by the Dev 1 Alarm contract.
        self.assertEqual(self.cur.fetchone(),
                         ("DOOR_OPEN_OUTSIDE_HOURS", "DOOR_OPENED"))

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

    def test_s1_suspicious_device_timestamp_is_not_evaluated(self):
        received = self.t0 + timedelta(hours=1)
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=99,
                     measured_at=self.t0, received_at=received)
        self.cur.execute("""SELECT count(*) FROM rule_runtime_state
            WHERE rule_code='S1_TEMP_HIGH' AND device_id='TH-001'""")
        self.assertEqual(self.cur.fetchone()[0], 0)

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

    def test_manual_close_cannot_retrigger_before_physical_recovery(self):
        for s in (0, 60, 120, 180, 240, 300):
            on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                         measured_at=self.t0 + timedelta(seconds=s),
                         received_at=self.t0 + timedelta(seconds=s))
        self.cur.execute("""SELECT a.episode_key,m.wire_alarm_key,m.episode_started_at
            FROM alarm_occurrences a JOIN alarm_episode_mappings m
              ON m.internal_episode_key=a.episode_key
            WHERE a.rule_code='S1_TEMP_HIGH' AND a.occurrence_kind='TRIGGERED'""")
        episode, wire_key, started = self.cur.fetchone()
        self.cur.execute("""INSERT INTO alarm_lifecycle
            (wire_alarm_key,org_code,state,state_rank,version,updated_at)
            VALUES(%s,'demo','closed',3,1,%s)""", (wire_key, self.t0 + timedelta(seconds=301)))
        # Model a process recovery edge where the threshold state would otherwise
        # try to trigger again while the same physical violation is still present.
        self.cur.execute("""UPDATE rule_runtime_state SET is_active=FALSE,
            hold_started_at=%s,last_sample_at=%s,episode_key=%s,episode_started_at=%s
            WHERE rule_code='S1_TEMP_HIGH' AND device_id='TH-001'""",
            (self.t0, self.t0 + timedelta(seconds=300), episode, started))
        on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=31,
                     measured_at=self.t0 + timedelta(seconds=360),
                     received_at=self.t0 + timedelta(seconds=360))
        self.cur.execute("""SELECT count(*) FROM alarm_occurrences
            WHERE rule_code='S1_TEMP_HIGH' AND occurrence_kind='TRIGGERED'""")
        self.assertEqual(self.cur.fetchone()[0], 1)
        self.cur.execute("""SELECT is_active,episode_key FROM rule_runtime_state
            WHERE rule_code='S1_TEMP_HIGH' AND device_id='TH-001'""")
        self.assertEqual(self.cur.fetchone(), (True, episode))
        for s in (361, 421, 481):
            on_telemetry(self.cur, device_id="TH-001", metric="temperature", value=28,
                         measured_at=self.t0 + timedelta(seconds=s),
                         received_at=self.t0 + timedelta(seconds=s))
        self.cur.execute("""SELECT count(*) FROM alarm_occurrences
            WHERE episode_key=%s AND occurrence_kind='CLEARED'""", (episode,))
        self.assertEqual(self.cur.fetchone()[0], 1)


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1")
class ExternalAlarmCallbackDatabaseTests(unittest.TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex
        self.internal = f"S1_TEMP_HIGH:TH-001:{suffix}"
        self.external = f"NW-LOCAL-RULE-S1:NW-LOCAL-TH001:{suffix}"
        self.episode = f"ep:callback:{suffix}"
        self.occurrence = f"m1:callback:{suffix}"
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO alarm_episode_mappings
                    (internal_episode_key,wire_alarm_key,episode_started_at,rule_code,device_id)
                    VALUES(%s,%s,%s,'S1_TEMP_HIGH','TH-001')""",
                    (self.episode,self.internal,datetime(2026,10,30,3,0)))
                cur.execute("""INSERT INTO alarm_occurrences
                    (alarm_key,episode_key,occurrence_kind,rule_code,device_id,severity,
                     event_type,triggered_at,payload_json)
                    VALUES(%s,%s,'TRIGGERED','S1_TEMP_HIGH','TH-001','HIGH','TEST',%s,'{}')""",
                    (self.occurrence,self.episode,datetime(2026,10,30,3,0)))
                cur.execute("""INSERT INTO alarm_outbox
                    (alarm_key,wire_alarm_key,payload_json,next_attempt_at,created_at)
                    VALUES(%s,%s,%s::jsonb,%s,%s)""",
                    (self.occurrence,self.internal,
                     '{"kind":"occurrence","alarm_key":"' + self.external + '",'
                     '"rule_id":"NW-LOCAL-RULE-S1","device_id":"NW-LOCAL-TH001"}',
                     datetime(2026,10,30,3,0),datetime(2026,10,30,3,0)))
            conn.commit()

    def tearDown(self):
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM alarm_lifecycle WHERE wire_alarm_key=%s",(self.internal,))
                cur.execute("DELETE FROM alarm_outbox WHERE alarm_key=%s",(self.occurrence,))
                cur.execute("DELETE FROM alarm_occurrences WHERE alarm_key=%s",(self.occurrence,))
                cur.execute("DELETE FROM alarm_episode_mappings WHERE wire_alarm_key=%s",(self.internal,))
            conn.commit()

    def test_external_if06_key_closes_internal_episode_and_suppresses_retrigger(self):
        body=AlarmStateIn(alarm_key=self.external,org_code="demo",state="closed",
                          changed_at=datetime(2026,10,30,3,1),changed_by="dev1:user",version=1)
        result=alarm_state(self.external,body,org="demo")
        self.assertEqual(result["resource_id"],self.external)
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM alarm_lifecycle WHERE wire_alarm_key=%s",(self.internal,))
                self.assertEqual(cur.fetchone()[0],"closed")
                self.assertEqual(_manually_closed_uncleared_episode(
                    cur,"S1_TEMP_HIGH","TH-001")[0],self.episode)


if __name__ == "__main__":
    unittest.main()
