import os
import unittest
from datetime import datetime
from unittest.mock import patch

from integrations.nextgrp import DeliveryResult, FrappeAlarmAdapter, NextGrpAdapter
from outbox.worker import (OutboxStore, OutboxWorker, alarm_adapter, bridge_settings,
                           approved_delivery_selector, delivery_not_before,
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

    def persist_payload(self, row_id, worker_id, payload):
        self.persisted_payload = payload
        return True


class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.calls = []

    def send(self, key, payload):
        self.calls.append((key, payload))
        if self.error:
            raise self.error
        return self.result


class CapturingCursor:
    def __init__(self): self.sql = ""
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, params): self.sql = sql
    def fetchall(self): return []


class CapturingConnection:
    def __init__(self): self.cursor_value = CapturingCursor()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self.cursor_value
    def commit(self): pass


class OutboxUnitTests(unittest.TestCase):
    def test_clear_ordering_guard_applies_only_to_alarm_outbox(self):
        alarm_conn = CapturingConnection()
        OutboxStore(lambda: alarm_conn).claim("w", 2, 30, datetime(2035, 1, 1))
        self.assertIn("prior.delivery_state<>'DELIVERED'", alarm_conn.cursor_value.sql)
        status_conn = CapturingConnection()
        OutboxStore(lambda: status_conn, table="status_outbox", key_column="device_id").claim(
            "w", 2, 30, datetime(2035, 1, 1))
        self.assertNotIn("wire_alarm_key", status_conn.cursor_value.sql)

    def test_exact_if05_selector_is_fail_closed(self):
        with patch.dict(os.environ, {"IF05_APPROVED_OUTBOX_IDS": "949,950",
                "IF05_APPROVED_ORG": "hainam", "IF05_APPROVED_DEVICE": "TH-001",
                "IF05_APPROVED_RULE": "S1_TEMP_HIGH", "IF05_RUN_ONCE": "true"}, clear=True):
            ids, values = approved_delivery_selector()
            self.assertEqual(ids, (949, 950))
            self.assertEqual(values["IF05_APPROVED_ORG"], "hainam")
        with patch.dict(os.environ, {"IF05_APPROVED_OUTBOX_IDS": "949"}, clear=True):
            with self.assertRaises(ValueError):
                approved_delivery_selector()

    def test_production_transient_retry_has_no_attempt_ceiling(self):
        store = FakeStore([(1, "alarm-1", {"alarm_key": "alarm-1"}, 99)])
        worker = OutboxWorker(store, FakeAdapter(error=TimeoutError("timeout")),
                              worker_id="w", max_attempts=None)
        worker.run_once(datetime(2035, 1, 1))
        self.assertIsNone(store.failures[0][0][6])
        self.assertFalse(store.failures[0][1]["force_terminal"])

    def test_claim_selector_filters_id_tenant_device_and_rule(self):
        conn = CapturingConnection()
        OutboxStore(lambda: conn, approved_ids=(949,), approved_org="hainam",
            approved_device="TH-001", approved_rule="S1_TEMP_HIGH").claim(
                "w", 1, 30, datetime(2035, 1, 1))
        self.assertIn("id=ANY", conn.cursor_value.sql)
        self.assertIn("payload_json->>'org_code'", conn.cursor_value.sql)
        self.assertIn("payload_json->>'device_id'", conn.cursor_value.sql)
        self.assertIn("payload_json->>'rule_id'", conn.cursor_value.sql)

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

    def test_frozen_wire_payload_is_not_remapped_on_retry(self):
        payload = {"alarm_key": "external-episode", "event_id": "stable-event"}
        store = FakeStore([(1, "internal-row", payload, 2, "internal-episode")])
        adapter = FakeAdapter(DeliveryResult(200, {"accepted": True,
            "alarm_key": "external-episode", "event_id": "stable-event", "ack_id": "ack"}))
        transforms = []
        worker = OutboxWorker(store, adapter, worker_id="w",
            payload_transform=lambda body: transforms.append(body) or {"alarm_key": "changed"})
        worker.run_once(datetime(2035, 1, 1))
        self.assertEqual(transforms, [])
        self.assertEqual(adapter.calls[0][1], payload)
        self.assertEqual(len(store.deliveries), 1)

    def test_http_policy_matches_urd_for_every_relevant_status_class(self):
        for status, terminal in ((400, True), (401, True), (403, True), (404, True),
                                 (408, False), (409, True), (415, True), (417, True), (422, True),
                                 (429, False), (500, False), (503, False)):
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

    def test_frappe_ack_and_duplicate_use_message_wrapper(self):
        payload = {"kind": "occurrence", "device_id": "NW-IT-TH001", "severity": "High"}
        first = {"message": {"alarm_key": "ALARM-DOC-1", "kind": "occurrence",
            "device_id": "NW-IT-TH001", "severity": "High", "status": "New",
            "occurrence_count": 1, "changed": True, "duplicate": False}}
        duplicate = {"message": {**first["message"], "changed": False,
            "duplicate": True, "replayed": True}}
        self.assertTrue(FrappeAlarmAdapter.validate_ack(first, payload, "event-1"))
        self.assertTrue(FrappeAlarmAdapter.validate_ack(duplicate, payload, "event-1"))
        self.assertFalse(FrappeAlarmAdapter.validate_ack(
            {"accepted": True, "alarm_key": "wire-1", "ack_id": "mock"},
            "wire-1", "event-1"))

    def test_frappe_success_uses_event_id_as_stable_receipt(self):
        body = {"message": {"alarm_key": "ALARM-DOC-1", "kind": "occurrence",
            "device_id": "NW-IT-TH001", "severity": "High", "status": "New",
            "occurrence_count": 1, "changed": True, "duplicate": False}}
        adapter = FakeAdapter(DeliveryResult(200, body))
        adapter.validate_ack = FrappeAlarmAdapter.validate_ack
        adapter.delivery_receipt = FrappeAlarmAdapter.delivery_receipt
        store = FakeStore([(1, "internal-1", {
            "alarm_key": "wire-1", "event_id": "event-1", "kind": "occurrence",
            "device_id": "NW-IT-TH001", "severity": "High"}, 1)])
        OutboxWorker(store, adapter, worker_id="w").run_once(datetime(2035, 1, 1))
        self.assertEqual(store.deliveries[0][2], "ALARM-DOC-1")
        self.assertFalse(store.failures)

    def test_frappe_invalid_response_is_not_delivered(self):
        adapter = FakeAdapter(DeliveryResult(200, {"message": {
            "alarm_key": "wire-1", "changed": True, "duplicate": False}}))
        adapter.validate_ack = FrappeAlarmAdapter.validate_ack
        adapter.delivery_receipt = FrappeAlarmAdapter.delivery_receipt
        store = FakeStore([(1, "internal-1", {
            "alarm_key": "wire-1", "event_id": "event-1"}, 1)])
        OutboxWorker(store, adapter, worker_id="w").run_once(datetime(2035, 1, 1))
        self.assertFalse(store.deliveries)
        self.assertEqual(len(store.failures), 1)

    def test_frappe_protocol_is_explicit_and_real_only(self):
        with patch.dict(os.environ, {"BRIDGE_ALARM_PROTOCOL": "frappe",
                "FRAPPE_SITE_NAME": "nextgrp.localhost"}, clear=True):
            with self.assertRaises(ValueError):
                alarm_adapter("mock", "https://example.test/receive_alarm", "key:secret", 1, False)
            adapter = alarm_adapter("real", "https://example.test/receive_alarm",
                                    "key:secret", 1, False)
            self.assertIsInstance(adapter, FrappeAlarmAdapter)


if __name__ == "__main__":
    unittest.main()
