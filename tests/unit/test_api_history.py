import unittest
from datetime import datetime, timezone

from api.application import decode_cursor, encode_cursor, utc_parameter


class ApiHistoryTests(unittest.TestCase):
    def test_cursor_round_trip_is_opaque_and_stable(self):
        stamp = datetime(2026, 10, 30, 3, 0, 0)
        cursor = encode_cursor(stamp, "temperature")
        self.assertNotIn("2026-10-30", cursor)
        self.assertEqual(decode_cursor(cursor), (stamp, "temperature"))

    def test_time_filter_is_normalized_to_naive_utc(self):
        local = datetime.fromisoformat("2026-10-30T10:00:00+07:00")
        self.assertEqual(utc_parameter(local, "from"), datetime(2026, 10, 30, 3, 0, 0))
        self.assertEqual(local.astimezone(timezone.utc).hour, 3)


if __name__ == "__main__":
    unittest.main()
