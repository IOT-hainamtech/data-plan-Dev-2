import unittest
from unittest.mock import patch

from snapshot_worker import run_reconciliation_cycle


class SnapshotWorkerTests(unittest.TestCase):
    def test_config_failure_does_not_suppress_status_reconciliation(self):
        calls = []
        with patch("snapshot_worker.LOG.exception"):
            run_reconciliation_cycle(
                lambda: (_ for _ in ()).throw(ConnectionError("Dev 1 unavailable")),
                lambda: calls.append("status"),
            )
        self.assertEqual(calls, ["status"])


if __name__ == "__main__":
    unittest.main()
