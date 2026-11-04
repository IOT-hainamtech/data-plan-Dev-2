import os
import unittest
from unittest.mock import patch

from integrations.frappe_client import FrappeResponse
from realtime_worker import RealtimeAdapter, armed_selector, map_event_payload
from outbox.worker import OutboxStore


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.payload = None

    def post_device_event(self, payload):
        self.payload = payload
        return self.response

    def post_status_json(self, payload):
        self.payload = payload
        return self.response


class RealtimeDeliveryTests(unittest.TestCase):
    def test_s1_and_s6_payload_mapping_preserves_event_identity_and_time(self):
        class Context:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def cursor(self): return self
        source = {"org_code": "hainam", "device_id": "TH-001", "event_id": "evt-s1",
                  "event_type": "TELEMETRY_UPDATED", "event_at": "2026-11-03T09:00:00Z",
                  "metrics": [{"code": "temperature", "value": 27.5, "unit": "Cel"}],
                  "data_status": "LIVE"}
        with patch("realtime_worker.db_connection", return_value=Context()), \
             patch("realtime_worker._mapping", return_value="NW-IT-TH001"):
            mapped = map_event_payload(source)
        self.assertEqual((mapped["device_id"], mapped["event_id"], mapped["event_at"]),
                         ("NW-IT-TH001", "evt-s1", "2026-11-03T16:00:00+07:00"))
        self.assertEqual(mapped["metrics"][0]["unit"], "\u00b0C")
        door = {"org_code": "hainam", "device_id": "DR-001", "event_id": "evt-s6",
                "event_type": "DOOR_CLOSED", "event_at": "2026-11-03T09:00:01Z",
                "door_state": "closed", "data_status": "LIVE"}
        with patch("realtime_worker.db_connection", return_value=Context()), \
             patch("realtime_worker._mapping", return_value="NW-IT-DR001"):
            mapped_door = map_event_payload(door)
        self.assertEqual((mapped_door["device_id"], mapped_door["door_state"]),
                         ("NW-IT-DR001", "closed"))

    def test_status_claim_requires_real_transition(self):
        store = OutboxStore(table="status_outbox", key_column="delivery_event_id",
                            approved_org="hainam", approved_devices=("TH-001", "DR-001"))
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def execute(self, sql, params):
                self.sql, self.params = sql, params
            def fetchall(self): return []
        class Connection:
            def __init__(self): self.cursor_value = Cursor()
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def cursor(self): return self.cursor_value
            def commit(self): pass
        connection = Connection()
        store.connection_factory = lambda: connection
        from datetime import datetime
        store.claim("worker", 10, 30, datetime(2026, 11, 3))
        self.assertIn("payload_json->>'transition'='true'", connection.cursor_value.sql)

    def test_arm_requires_exact_high_water_time_and_scope(self):
        valid = {"REALTIME_E2E_ARMED": "true", "REALTIME_EVENT_HIGH_WATER_ID": "12",
            "REALTIME_STATUS_HIGH_WATER_ID": "530", "REALTIME_ARMED_AT": "2026-11-03T09:00:00Z",
            "REALTIME_ORG": "hainam", "REALTIME_DEVICES": "TH-001,DR-001"}
        with patch.dict(os.environ, valid, clear=True):
            result = armed_selector()
            self.assertEqual((result["event_mark"], result["status_mark"]), (12, 530))
        for key, value in (("REALTIME_ORG", "demo"), ("REALTIME_DEVICES", "TH-001"),
                           ("REALTIME_ARMED_AT", "2026-11-03T09:00:00")):
            with patch.dict(os.environ, {**valid, key: value}, clear=True):
                with self.assertRaises(ValueError):
                    armed_selector()

    def test_event_ack_matches_external_and_internal_identity(self):
        payload = {"device_id": "NW-IT-DR001", "_data_plane_device_id": "DR-001",
                   "event_id": "evt-1", "event_type": "DOOR_OPENED",
                   "event_at": "2026-11-03T16:00:00+07:00", "door_state": "open",
                   "data_status": "LIVE", "_wire_frozen": True}
        message = {"accepted": True, "device_id": "NW-IT-DR001",
                   "data_plane_device_id": "DR-001", "event_id": "evt-1",
                   "event_type": "DOOR_OPENED"}
        client = FakeClient(FrappeResponse(200, message, {"message": message}))
        adapter = RealtimeAdapter(client, "event")
        result = adapter.send("evt-1", payload)
        self.assertNotIn("_wire_frozen", client.payload)
        self.assertTrue(adapter.validate_ack(result.body, payload, "evt-1"))
        result.body["message"]["event_id"] = "wrong"
        self.assertFalse(adapter.validate_ack(result.body, payload, "evt-1"))

    def test_status_changed_false_is_valid_ack(self):
        payload = {"device_id": "NW-IT-TH001", "_data_plane_device_id": "TH-001",
                   "state": "offline", "_wire_frozen": True}
        message = {"device_id": "NW-IT-TH001", "previous_state": "Offline",
                   "online_state": "Offline", "last_seen": "2026-11-03T09:00:00Z",
                   "changed": False}
        adapter = RealtimeAdapter(FakeClient(FrappeResponse(200, message, {"message": message})),
                                  "status")
        self.assertTrue(adapter.validate_ack({"message": message}, payload))
        message["online_state"] = "online"
        self.assertFalse(adapter.validate_ack({"message": message}, payload))


if __name__ == "__main__":
    unittest.main()
