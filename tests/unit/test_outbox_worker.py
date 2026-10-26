import os
import unittest
from datetime import datetime
from unittest.mock import patch

from integrations.nextgrp import DeliveryResult, NextGrpAdapter
from outbox.worker import (OutboxWorker, bridge_settings, delivery_not_before,
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

    def failed(self, *args, **kwargs):
        self.failures.append((args, kwargs))
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
    def test_real_mode_requires_real_urls_and_token(self):
        with patch.dict(os.environ, {"BRIDGE_MODE": "real",
                "BRIDGE_ALARM_URL": "http://mock-nextgrp:8080/internal/events/alarm",
                "BRIDGE_STATUS_URL": "http://mock-nextgrp:8080/internal/events/status",
                "BRIDGE_OUTBOUND_TOKEN": "real-token"}, clear=True):
            with self.assertRaises(ValueError):
                bridge_settings()
        with patch.dict(os.environ, {"BRIDGE_MODE": "real",
                "BRIDGE_ALARM_URL": "http://192.168.1.83:8081/internal/events/alarm",
                "BRIDGE_STATUS_URL": "http://192.168.1.83:8081/internal/events/status"}, clear=True):
            with self.assertRaises(ValueError):
                bridge_settings()
        with patch.dict(os.environ, {"BRIDGE_MODE": "real",
                "BRIDGE_ALARM_URL": "http://192.168.1.83:8081/internal/events/alarm",
                "BRIDGE_STATUS_URL": "http://192.168.1.83:8081/internal/events/status",
                "BRIDGE_OUTBOUND_TOKEN": "real-token"}, clear=True):
            self.assertEqual(bridge_settings()[0], "real")

    def test_delivery_cutoff_requires_timezone(self):
        with patch.dict(os.environ, {"BRIDGE_DELIVER_NOT_BEFORE": "2026-10-23T10:30:00Z"}, clear=True):
            self.assertEqual(delivery_not_before(), datetime(2026, 10, 23, 10, 30))
        with patch.dict(os.environ, {"BRIDGE_DELIVER_NOT_BEFORE": "2026-10-23T10:30:00"}, clear=True):
            with self.assertRaises(ValueError):
                delivery_not_before()

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

    def test_http_policy_retries_500_but_terminates_401_and_422(self):
        for status, terminal in ((500, False), (401, True), (422, True)):
            store = FakeStore([(1, "alarm-1", {"alarm_key": "alarm-1"}, 1)])
            adapter = FakeAdapter(DeliveryResult(status, {"detail": "test"}))
            adapter.require_202 = True
            OutboxWorker(store, adapter, worker_id="w").run_once(datetime(2035, 1, 1))
            self.assertEqual(store.failures[0][1]["force_terminal"], terminal)

    def test_https_required_except_explicit_mock_mode(self):
        with self.assertRaises(ValueError):
            NextGrpAdapter("http://example.test", 1, "none")
        NextGrpAdapter("http://mock-nextgrp/mock/v1/alarms", 1, "none",
                       allow_insecure_mock=True)


if __name__ == "__main__":
    unittest.main()
