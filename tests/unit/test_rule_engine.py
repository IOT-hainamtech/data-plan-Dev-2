"""Pure Rule Engine state-machine tests."""
import unittest
from datetime import datetime, time, timedelta

from rules.engine import (_alarm_key, _manually_closed_uncleared_episode, _write_occurrence, door_should_fire,
                           is_business_hours, threshold_transition)


class OccurrenceCursor:
    """Small transaction-local cursor double; no runtime database is used."""
    def __init__(self, mapping=None, occurrence_count=0, rule_version=3):
        self.mapping = mapping
        self.occurrence_count = occurrence_count
        self.rule_version = rule_version
        self.rowcount = 0
        self.result = None
        self.occurrence_payload = None
        self.outbox_payload = None

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.rowcount = 0
        if normalized.startswith("INSERT INTO alarm_episode_mappings"):
            if self.mapping is None:
                self.mapping = (params[1], params[2])
            self.rowcount = 1
        elif normalized.startswith("SELECT wire_alarm_key"):
            self.result = self.mapping
        elif normalized.startswith("SELECT count(*) FROM alarm_occurrences"):
            self.result = (self.occurrence_count,)
        elif normalized.startswith("SELECT d.org_code"):
            self.result = ("org", "zone", self.rule_version)
        elif normalized.startswith("INSERT INTO alarm_occurrences"):
            self.occurrence_payload = getattr(params[-1], "obj", params[-1])
            self.rowcount = 1
            self.occurrence_count += 1
        elif normalized.startswith("INSERT INTO alarm_outbox"):
            self.outbox_payload = getattr(params[1], "obj", params[1])
            self.rowcount = 1
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.result


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 10, 14, 12, 0)

    def step(self, value, seconds, entered=None, recovery=None, active=False,
             hold=300, clear_hold=120):
        return threshold_transition(
            value=value, threshold=30.0, hysteresis=1.0, hold_seconds=hold,
            clear_hold_seconds=clear_hold, observed_at=self.t0 + timedelta(seconds=seconds),
            entered_at=entered, recovery_started_at=recovery, active=active)

    def test_requires_300_continuous_seconds(self):
        entered, _, active, transition = self.step(31, 0)
        self.assertEqual((active, transition), (False, None))
        self.assertEqual(self.step(31, 299, entered=entered)[3], None)
        self.assertEqual(self.step(31, 300, entered=entered)[2:], (True, "TRIGGERED"))

    def test_hold_resets_at_threshold(self):
        entered = self.step(31, 0)[0]
        self.assertEqual(self.step(30, 200, entered=entered), (None, None, False, None))

    def test_hysteresis_and_120_second_recovery(self):
        self.assertEqual(self.step(29.5, 0, active=True), (None, None, True, None))
        recovery = self.step(28.9, 0, active=True)[1]
        self.assertEqual(self.step(28.5, 119, recovery=recovery, active=True)[3], None)
        self.assertEqual(self.step(28.5, 120, recovery=recovery, active=True)[3], "CLEARED")

    def test_recovery_resets_if_temperature_rises(self):
        recovery = self.step(28.5, 0, active=True)[1]
        self.assertEqual(self.step(29.0, 60, recovery=recovery, active=True),
                         (None, None, True, None))

    def test_alarm_key_is_idempotent_per_occurrence_kind(self):
        one = _alarm_key("S1", "D1", "episode", "TRIGGERED")
        self.assertEqual(one, _alarm_key("S1", "D1", "episode", "TRIGGERED"))
        self.assertNotEqual(one, _alarm_key("S1", "D1", "episode", "CLEARED"))


class DoorTests(unittest.TestCase):
    def setUp(self):
        self.start = time(8)
        self.end = time(18)

    def test_outside_hours_fires_immediately(self):
        now = datetime(2026, 10, 14, 0, 30)
        self.assertFalse(is_business_hours(now, self.start, self.end, "Asia/Ho_Chi_Minh"))
        self.assertTrue(door_should_fire(opened_at=now, now=now, hold_seconds=300,
            business_start=self.start, business_end=self.end,
            schedule_timezone="Asia/Ho_Chi_Minh"))

    def test_business_hours_requires_300_seconds(self):
        opened = datetime(2026, 10, 14, 2, 0)
        kwargs = dict(opened_at=opened, hold_seconds=300, business_start=self.start,
                      business_end=self.end, schedule_timezone="Asia/Ho_Chi_Minh")
        self.assertFalse(door_should_fire(now=opened + timedelta(seconds=299), **kwargs))
        self.assertTrue(door_should_fire(now=opened + timedelta(seconds=300), **kwargs))

    def test_weekend_is_always_outside_business_hours(self):
        # 2026-10-17 is Saturday; 02:00 UTC is 09:00 in Ho Chi Minh City.
        saturday = datetime(2026, 10, 17, 2, 0)
        sunday = datetime(2026, 10, 18, 2, 0)
        for now in (saturday, sunday):
            self.assertFalse(is_business_hours(
                now, self.start, self.end, "Asia/Ho_Chi_Minh"))
            self.assertTrue(door_should_fire(
                opened_at=now, now=now, hold_seconds=300,
                business_start=self.start, business_end=self.end,
                schedule_timezone="Asia/Ho_Chi_Minh"))

    def test_exact_business_hours_boundaries(self):
        # Asia/Ho_Chi_Minh is UTC+7. Wednesday 2026-10-14.
        # 08:00:00 local = 01:00:00 UTC (start of business hours)
        t_0800 = datetime(2026, 10, 14, 1, 0, 0)
        self.assertTrue(is_business_hours(t_0800, self.start, self.end, "Asia/Ho_Chi_Minh"))

        # 07:59:59 local = 00:59:59 UTC (1s before business hours)
        t_0759 = datetime(2026, 10, 14, 0, 59, 59)
        self.assertFalse(is_business_hours(t_0759, self.start, self.end, "Asia/Ho_Chi_Minh"))

        # 17:59:59 local = 10:59:59 UTC (last second of business hours)
        t_1759 = datetime(2026, 10, 14, 10, 59, 59)
        self.assertTrue(is_business_hours(t_1759, self.start, self.end, "Asia/Ho_Chi_Minh"))

        # 18:00:00 local = 11:00:00 UTC (exact end of business hours, exclusive)
        t_1800 = datetime(2026, 10, 14, 11, 0, 0)
        self.assertFalse(is_business_hours(t_1800, self.start, self.end, "Asia/Ho_Chi_Minh"))

    def test_friday_to_saturday_midnight_transition(self):
        # Friday 23:59:59 local = Friday 16:59:59 UTC
        fri_night = datetime(2026, 10, 16, 16, 59, 59)
        self.assertFalse(is_business_hours(fri_night, self.start, self.end, "Asia/Ho_Chi_Minh"))

        # Saturday 00:00:00 local = Friday 17:00:00 UTC
        sat_midnight = datetime(2026, 10, 16, 17, 0, 0)
        self.assertFalse(is_business_hours(sat_midnight, self.start, self.end, "Asia/Ho_Chi_Minh"))


class AlarmContractTests(unittest.TestCase):
    def setUp(self):
        self.triggered = datetime(2026, 10, 29, 9, 27)
        self.cleared = self.triggered + timedelta(minutes=13)
        self.episode = "ep:S6:one"

    def write(self, cursor, kind, observed):
        return _write_occurrence(cursor, rule_code="S6_DOOR_OPEN", device_id="DR-001",
            severity="WARNING", occurrence_kind=kind, observed_at=observed,
            episode_key=self.episode, episode_started_at=self.triggered,
            event_type="DOOR_OPENED" if kind == "TRIGGERED" else "DOOR_CLOSED",
            details={"device_event_id": f"source-{kind.lower()}"})

    def test_trigger_and_clear_share_wire_key_with_contiguous_sequence(self):
        cursor = OccurrenceCursor(rule_version=7)
        self.assertTrue(self.write(cursor, "TRIGGERED", self.triggered))
        trigger = cursor.occurrence_payload
        mapping = cursor.mapping
        self.assertEqual(trigger["kind"], "occurrence")
        self.assertEqual(trigger["occurrence_seq"], 1)
        self.assertEqual(trigger["rule_version"], 7)
        trigger_event_id = trigger["event_id"]

        cursor.occurrence_payload = None
        self.assertTrue(self.write(cursor, "CLEARED", self.cleared))
        clear = cursor.occurrence_payload
        self.assertEqual(cursor.mapping, mapping)
        self.assertEqual(clear["kind"], "cleared")
        self.assertEqual(clear["occurrence_seq"], 2)
        self.assertEqual(clear["alarm_key"], trigger["alarm_key"])
        self.assertEqual(clear["episode_key"], trigger["episode_key"])
        self.assertNotEqual(clear["event_id"], trigger_event_id)
        self.assertEqual(cursor.outbox_payload, clear)

    def test_clear_cannot_overtake_trigger_mapping(self):
        cursor = OccurrenceCursor()
        with self.assertRaisesRegex(ValueError, "mapping is missing"):
            self.write(cursor, "CLEARED", self.cleared)
        self.assertIsNone(cursor.occurrence_payload)

    def test_source_rule_version_zero_is_not_silently_rewritten(self):
        cursor = OccurrenceCursor(rule_version=0)
        self.write(cursor, "TRIGGERED", self.triggered)
        self.assertEqual(cursor.occurrence_payload["rule_version"], 0)

    def test_invalid_rule_version_fails_before_occurrence_insert(self):
        cursor = OccurrenceCursor(rule_version=-1)
        with self.assertRaisesRegex(ValueError, "config_version"):
            self.write(cursor, "TRIGGERED", self.triggered)
        self.assertIsNone(cursor.occurrence_payload)


class LifecycleSuppressionTests(unittest.TestCase):
    class Cursor:
        def __init__(self, result):
            self.result = result
            self.sql = None
            self.params = None

        def execute(self, sql, params=()):
            self.sql, self.params = " ".join(sql.split()), params

        def fetchone(self):
            return self.result

    def test_manual_close_lookup_requires_no_sensor_clear(self):
        expected = ("ep:S1:existing", datetime(2026, 10, 30, 8, 0))
        cursor = self.Cursor(expected)
        actual = _manually_closed_uncleared_episode(
            cursor, "S1_TEMP_HIGH", "TH-001")
        self.assertEqual(actual, expected)
        self.assertIn("l.state='closed'", cursor.sql)
        self.assertIn("a.occurrence_kind='CLEARED'", cursor.sql)
        self.assertEqual(cursor.params, ("S1_TEMP_HIGH", "TH-001"))


if __name__ == "__main__":
    unittest.main()
