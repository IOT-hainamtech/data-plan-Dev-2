import unittest
from datetime import datetime

from status.service import enqueue_status_reconciliation


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.inserted = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT d.device_id"):
            self.rowcount = 0
        elif normalized.startswith("INSERT INTO status_outbox"):
            self.inserted.append(params)
            self.rowcount = 1
        else:
            raise AssertionError(normalized)

    def fetchall(self):
        return self.rows


class StatusReconciliationTests(unittest.TestCase):
    def test_snapshot_identity_is_stable_within_five_minute_bucket(self):
        status_at = datetime(2026, 10, 30, 8, 0)
        rows = [("TH-001", "demo", "online", status_at, status_at, "abcdef12")]
        first, second = Cursor(rows), Cursor(rows)
        self.assertEqual(enqueue_status_reconciliation(first, status_at), 1)
        self.assertEqual(enqueue_status_reconciliation(second, status_at.replace(minute=4)), 1)
        self.assertEqual(first.inserted[0][0], second.inserted[0][0])
        payload = getattr(first.inserted[0][2], "obj", first.inserted[0][2])
        self.assertEqual(payload["state"], "online")
        self.assertEqual(set(payload), {
            "event_id", "org_code", "device_id", "state", "since", "last_seen", "reason"})


if __name__ == "__main__":
    unittest.main()
