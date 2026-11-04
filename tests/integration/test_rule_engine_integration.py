"""PostgreSQL integration tests; enable with RUN_DB_TESTS=1."""
import os
from pathlib import Path
import unittest
import uuid
from datetime import datetime, timedelta
import sqlparse
from psycopg.types.json import Jsonb

from api.integration import alarm_state
from api.models import AlarmStateIn
from core.database import db_connection
from registry.sync import apply_rule, payload_hash
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

    def test_s1_hainam_full_threshold_lifecycle_and_disabled_demo_rule(self):
        """Acceptance path for S1 without touching the live TH-001 database."""
        self.cur.execute("""INSERT INTO organizations(org_code,display_name)
            VALUES('hainam','Hai Nam') ON CONFLICT(org_code) DO NOTHING""")
        self.cur.execute("""INSERT INTO sites(org_code,site_code,display_name,timezone)
            VALUES('hainam','hq','HQ','Asia/Ho_Chi_Minh')
            ON CONFLICT(org_code,site_code) DO NOTHING""")
        self.cur.execute("""INSERT INTO zones(org_code,site_code,zone_code,display_name)
            VALUES('hainam','hq','office','Office')
            ON CONFLICT(org_code,site_code,zone_code) DO NOTHING""")
        self.cur.execute("""UPDATE devices SET org_code='hainam',site_code='hq',zone_code='office'
            WHERE device_id='TH-001'""")
        self.cur.execute("""INSERT INTO rule_definitions
            (rule_code,device_id,rule_kind,metric,threshold_value,hysteresis_value,
             hold_seconds,clear_hold_seconds,quiet_seconds,severity,enabled,scope_type,
             config_version,config_json)
            VALUES('S1_DEMO_TEMP_HIGH','TH-001','THRESHOLD','temperature',30,1,
                   0,120,0,'HIGH',FALSE,'DEVICE',2,'{"operator":">"}'::jsonb)
            ON CONFLICT(rule_code) DO UPDATE SET enabled=FALSE,config_version=2""")

        def sample(seconds, value):
            at = self.t0 + timedelta(seconds=seconds)
            return on_telemetry(self.cur, device_id="TH-001", metric="temperature",
                                value=value, measured_at=at, received_at=at)

        self.assertEqual(sample(0, 29.5), 0)       # below threshold
        for seconds in (10, 70, 130, 190, 250):  # above, not yet 300 seconds
            self.assertEqual(sample(seconds, 31.0), 0)
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE rule_code='S1_TEMP_HIGH'")
        self.assertEqual(self.cur.fetchone()[0], 0)

        self.assertEqual(sample(310, 31.0), 1)     # exactly 300 seconds continuous
        self.assertEqual(sample(370, 31.5), 0)     # no duplicate while still active
        with self.conn.cursor() as restarted:
            restarted.execute("""SELECT is_active,episode_key FROM rule_runtime_state
                WHERE rule_code='S1_TEMP_HIGH' AND device_id='TH-001'""")
            active, episode = restarted.fetchone()
            self.assertTrue(active)
            self.assertIsNotNone(episode)

        self.assertEqual(sample(371, 28.9), 0)     # below 30 - 1 hysteresis
        self.assertEqual(sample(431, 28.8), 0)
        self.assertEqual(sample(491, 28.7), 1)     # 120-second clear hold

        self.cur.execute("""SELECT a.occurrence_kind,a.episode_key,m.wire_alarm_key,
                a.payload_json->>'org_code',a.payload_json->>'alarm_key',
                a.payload_json->>'occurrence_seq'
            FROM alarm_occurrences a JOIN alarm_episode_mappings m
              ON m.internal_episode_key=a.episode_key
            WHERE a.rule_code='S1_TEMP_HIGH' ORDER BY a.triggered_at""")
        rows = self.cur.fetchall()
        self.assertEqual([row[0] for row in rows], ["TRIGGERED", "CLEARED"])
        self.assertEqual(rows[0][1], rows[1][1])
        self.assertEqual(rows[0][2], rows[1][2])
        self.assertEqual(rows[0][2], rows[0][4])
        self.assertEqual(rows[1][2], rows[1][4])
        self.assertEqual([row[3] for row in rows], ["hainam", "hainam"])
        self.assertEqual([row[5] for row in rows], ["1", "2"])
        self.cur.execute("""SELECT count(*) FROM alarm_outbox
            WHERE wire_alarm_key=%s""", (rows[0][2],))
        self.assertEqual(self.cur.fetchone()[0], 2)
        self.cur.execute("""SELECT count(*) FROM alarm_occurrences
            WHERE rule_code='S1_DEMO_TEMP_HIGH'""")
        self.assertEqual(self.cur.fetchone()[0], 0)
        self.cur.execute("""SELECT count(*) FROM rule_runtime_state
            WHERE rule_code='S1_DEMO_TEMP_HIGH'""")
        self.assertEqual(self.cur.fetchone()[0], 0)

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

    def test_s6_v2_unscheduled_open_limit_restart_short_open_and_clear(self):
        self.cur.execute("""UPDATE rule_definitions SET config_version=2,hold_seconds=300,
            clear_hold_seconds=0,quiet_seconds=0,business_start=NULL,business_end=NULL,
            schedule_timezone=NULL,config_json='{"auto_close":false,"open_limit_s":300}'::jsonb
            WHERE rule_code='S6_DOOR_OPEN'""")

        # A short opening is recorded, then cancelled without any Alarm/CLEARED.
        on_event(self.cur, device_id="DR-001", event_id="v2-short-open",
                 event_type="DOOR_OPENED", received_at=self.t0,
                 payload={"data": {"door_state": "open"}})
        evaluate_due_rules(self.cur, self.t0 + timedelta(seconds=60))
        on_event(self.cur, device_id="DR-001", event_id="v2-short-close",
                 event_type="DOOR_CLOSED", received_at=self.t0 + timedelta(seconds=61),
                 payload={"data": {"door_state": "closed"}})
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE rule_code='S6_DOOR_OPEN'")
        self.assertEqual(self.cur.fetchone()[0], 0)

        opened = self.t0 + timedelta(seconds=100)
        on_event(self.cur, device_id="DR-001", event_id="v2-long-open",
                 event_type="DOOR_OPENED", received_at=opened,
                 payload={"data": {"door_state": "open"}})
        evaluate_due_rules(self.cur, opened + timedelta(seconds=299))
        self.cur.execute("SELECT count(*) FROM alarm_occurrences WHERE rule_code='S6_DOOR_OPEN'")
        self.assertEqual(self.cur.fetchone()[0], 0)

        # A new cursor represents process restart; persisted state completes the timer.
        with self.conn.cursor() as restarted:
            self.assertEqual(evaluate_due_rules(
                restarted, opened + timedelta(seconds=300)), 1)
            self.assertEqual(evaluate_due_rules(
                restarted, opened + timedelta(seconds=305)), 0)
        on_event(self.cur, device_id="DR-001", event_id="v2-long-close",
                 event_type="DOOR_CLOSED", received_at=opened + timedelta(seconds=306),
                 payload={"data": {"door_state": "closed"}})
        self.cur.execute("""SELECT occurrence_kind,payload_json->>'rule_version',
                payload_json->>'alarm_key',payload_json->>'event_id',
                payload_json->'event'->>'event_type'
            FROM alarm_occurrences WHERE rule_code='S6_DOOR_OPEN'
            ORDER BY occurrence_kind DESC""")
        rows = self.cur.fetchall()
        self.assertEqual([row[0] for row in rows], ["TRIGGERED", "CLEARED"])
        self.assertEqual([row[1] for row in rows], ["2", "2"])
        self.assertEqual(rows[0][2], rows[1][2])
        self.assertNotEqual(rows[0][3], rows[1][3])
        self.assertEqual([row[4] for row in rows], ["DOOR_OPENED", "DOOR_CLOSED"])

    def test_s6_timer_uses_server_clock_when_device_clock_is_fast(self):
        received = self.t0
        device_event_at = received + timedelta(seconds=90)
        on_event(self.cur, device_id="DR-001", event_id="fast-clock-open",
                 event_type="DOOR_OPENED", received_at=received,
                 observed_at=device_event_at, payload={"data": {"door_state": "open"}})

        self.assertEqual(evaluate_due_rules(
            self.cur, received + timedelta(seconds=299)), 0)
        self.assertEqual(evaluate_due_rules(
            self.cur, received + timedelta(seconds=300)), 1)
        self.cur.execute("""SELECT state_changed_at,episode_started_at
            FROM rule_runtime_state WHERE rule_code='S6_DOOR_OPEN' AND device_id='DR-001'""")
        self.assertEqual(self.cur.fetchone(), (device_event_at, received))

    def test_s6_timer_uses_server_clock_when_device_clock_is_slow(self):
        received = self.t0
        device_event_at = received - timedelta(seconds=90)
        on_event(self.cur, device_id="DR-001", event_id="slow-clock-open",
                 event_type="DOOR_OPENED", received_at=received,
                 observed_at=device_event_at, payload={"data": {"door_state": "open"}})

        self.assertEqual(evaluate_due_rules(
            self.cur, received + timedelta(seconds=299)), 0)
        self.assertEqual(evaluate_due_rules(
            self.cur, received + timedelta(seconds=300)), 1)
        self.cur.execute("""SELECT a.payload_json->'event'->'data'->>'opened_at',
                s.episode_started_at
            FROM alarm_occurrences a JOIN rule_runtime_state s
              ON s.rule_code=a.rule_code AND s.device_id=a.device_id
            WHERE a.rule_code='S6_DOOR_OPEN' AND a.occurrence_kind='TRIGGERED'""")
        opened_at, timer_started_at = self.cur.fetchone()
        self.assertEqual(opened_at, device_event_at.isoformat() + "Z")
        self.assertEqual(timer_started_at, received)

    def test_migration_003_does_not_overwrite_if03_rule_v2(self):
        self.cur.execute("""UPDATE rule_definitions SET config_version=2,hold_seconds=300,
            clear_hold_seconds=0,quiet_seconds=0,business_start=NULL,business_end=NULL,
            schedule_timezone=NULL WHERE rule_code='S6_DOOR_OPEN'""")
        migration = Path("/migrations/003_m1_rule_engine.sql").read_text(encoding="utf-8")
        for statement in sqlparse.split(migration):
            if statement.strip():
                self.cur.execute(statement)
        self.cur.execute("""SELECT config_version,hold_seconds,quiet_seconds,business_start,
            business_end,schedule_timezone FROM rule_definitions
            WHERE rule_code='S6_DOOR_OPEN'""")
        self.assertEqual(self.cur.fetchone(), (2, 300, 0, None, None, None))

    def test_duplicate_if03_reconciles_materialized_rule_drift(self):
        rule = {"rule_id": "S6_DOOR_OPEN", "version": 2, "org_code": "demo",
            "rule_kind": "STATE_CHANGE", "scope": {"type": "DEVICE", "device_id": "DR-001"},
            "metric": None, "event_type": "DOOR_OPENED", "threshold": None,
            "hysteresis": 0, "duration_s": 0, "open_limit_s": 300,
            "clear_duration_s": 0, "quiet_window_s": 0, "business_start": None,
            "business_end": None, "timezone": None, "severity": "Medium",
            "auto_close": False, "enabled": True}
        self.cur.execute("""UPDATE rule_definitions SET config_version=2,payload_hash=%s,
            config_json=%s,quiet_seconds=600,business_start='08:00',business_end='18:00',
            schedule_timezone='Asia/Ho_Chi_Minh' WHERE rule_code='S6_DOOR_OPEN'""",
            (payload_hash(rule), Jsonb(rule)))
        result = apply_rule(self.cur, rule)
        self.assertTrue(result["duplicate"])
        self.cur.execute("""SELECT hold_seconds,quiet_seconds,business_start,business_end,
            schedule_timezone FROM rule_definitions WHERE rule_code='S6_DOOR_OPEN'""")
        self.assertEqual(self.cur.fetchone(), (300, 0, None, None, None))

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
