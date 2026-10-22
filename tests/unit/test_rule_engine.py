"""Pure Rule Engine state-machine tests."""
import unittest
from datetime import datetime, time, timedelta

from rules.engine import (_alarm_key, door_should_fire, is_business_hours,
                          threshold_transition)


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


if __name__ == "__main__":
    unittest.main()
