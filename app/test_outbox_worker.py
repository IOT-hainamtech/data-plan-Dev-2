import unittest
from datetime import datetime

from outbox_worker import (DeliveryResult, NextGrpAdapter, OutboxWorker,
                           retry_delay, validate_ack)


class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.deliveries = []
        self.failures = []

    def claim(self, worker_id, batch_size, lease_seconds, at):
        rows, self.rows = self.rows[:batch_size], []
        return rows

    def delivered(self, *args):
        self.deliveries.append(args)
        return True

    def failed(self, *args):
        self.failures.append(args)
        return True, False, 2.0


class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.calls = []

    def send(self, key, payload):
        self.calls.append((key, payload))
        if self.error:
            raise self.error
        return self.result


class OutboxUnitTests(unittest.TestCase):
    def test_backoff_is_exponential_bounded_and_jittered(self):
        self.assertEqual(retry_delay(1, 2, 10, 0, lambda: 0.5), 2)
        self.assertEqual(retry_delay(3, 2, 10, 0, lambda: 0.5), 8)
        self.assertEqual(retry_delay(9, 2, 10, 0, lambda: 0.5), 10)
        self.assertEqual(retry_delay(1, 10, 100, .2, lambda: 0), 8)
        self.assertEqual(retry_delay(1, 10, 100, .2, lambda: 1), 12)

    def test_ack_must_match_key_and_have_ack_id(self):
        self.assertTrue(validate_ack({"accepted": True, "alarm_key": "a", "ack_id": "1"}, "a"))
        self.assertFalse(validate_ack({"accepted": True, "alarm_key": "b", "ack_id": "1"}, "a"))
        self.assertFalse(validate_ack({"accepted": True, "alarm_key": "a"}, "a"))

    def test_success_and_duplicate_ack_are_delivered(self):
        for duplicate in (False, True):
            store = FakeStore([(1, "alarm-1", {"alarm_key": "alarm-1"}, 1)])
            adapter = FakeAdapter(DeliveryResult(200, {"accepted": True, "duplicate": duplicate,
                                                        "alarm_key": "alarm-1", "ack_id": "ack-1"}))
            OutboxWorker(store, adapter, worker_id="w").run_once(datetime(2035, 1, 1))
            self.assertEqual(len(store.deliveries), 1)
            self.assertFalse(store.failures)

    def test_network_error_timeout_and_invalid_ack_are_retried(self):
        cases = [FakeAdapter(error=OSError("network")), FakeAdapter(error=TimeoutError("timeout")),
                 FakeAdapter(DeliveryResult(200, {"accepted": True, "alarm_key": "wrong",
                                                   "ack_id": "ack"}))]
        for adapter in cases:
            store = FakeStore([(1, "alarm-1", {"alarm_key": "alarm-1"}, 1)])
            OutboxWorker(store, adapter, worker_id="w").run_once(datetime(2035, 1, 1))
            self.assertEqual(len(store.failures), 1)
            self.assertFalse(store.deliveries)

    def test_https_required_except_explicit_mock_mode(self):
        with self.assertRaises(ValueError):
            NextGrpAdapter("http://example.test", 1, "none")
        NextGrpAdapter("http://mock-nextgrp/mock/v1/alarms", 1, "none",
                       allow_insecure_mock=True)


if __name__ == "__main__":
    unittest.main()
