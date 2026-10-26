"""Black-box contract checks for the development Mock Bridge."""
import json
import os
import unittest
import urllib.error
import urllib.request
import uuid


@unittest.skipUnless(os.getenv("RUN_HTTP_TESTS") == "1", "set RUN_HTTP_TESTS=1")
class BridgeContractTests(unittest.TestCase):
    base = os.getenv("BRIDGE_TEST_URL", "http://mock-nextgrp:8080")
    token = os.getenv("MOCK_TEST_TOKEN", "mock-development-token")

    def post(self, path, payload, *, token=None, event_id=None):
        headers = {"Content-Type": "application/json",
                   "Idempotency-Key": event_id or payload.get("event_id", "missing")}
        if token is not False:
            headers["Authorization"] = f"Bearer {token or self.token}"
        request = urllib.request.Request(self.base + path, json.dumps(payload).encode(),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def alarm(self, kind="occurrence"):
        event_id = str(uuid.uuid4())
        alarm_key = f"S1:TH-001:{uuid.uuid4().hex}"
        payload = {"event_id": event_id, "org_code": "demo", "alarm_key": alarm_key,
                   "episode_key": alarm_key, "kind": kind, "rule_id": "S1_TEMP_HIGH",
                   "rule_version": 1, "device_id": "TH-001", "zone_code": "server-room",
                   "severity": "High", "episode_start": "2026-10-22T03:00:00Z",
                   "occurrence_seq": 1, "event": {"event_id": str(uuid.uuid4()),
                   "event_type": "TEMPERATURE_HIGH" if kind == "occurrence" else "TEMPERATURE_RECOVERED",
                   "ts": "2026-10-22T03:05:00Z", "data": {"summary": "contract test"}}}
        payload["raised_at" if kind == "occurrence" else "cleared_at"] = "2026-10-22T03:05:00Z"
        return payload

    def status(self, state):
        return {"event_id": str(uuid.uuid4()), "org_code": "demo", "device_id": "TH-001",
                "state": state, "since": "2026-10-22T03:20:05Z",
                "last_seen": "2026-10-22T03:19:00Z",
                "reason": "timeout" if state == "offline" else "telemetry"}

    def test_alarm_occurrence_clear_and_duplicate_are_accepted(self):
        for payload in (self.alarm(), self.alarm("cleared")):
            first = self.post("/internal/events/alarm", payload)
            duplicate = self.post("/internal/events/alarm", payload)
            self.assertEqual(first[0], 202, first)
            self.assertFalse(first[1]["duplicate"])
            self.assertTrue(duplicate[1]["duplicate"])
            self.assertEqual(first[1]["ack_id"], duplicate[1]["ack_id"])

    def test_offline_and_recovery_status_are_accepted(self):
        for state in ("offline", "online"):
            result = self.post("/internal/events/status", self.status(state))
            self.assertEqual(result[0], 202, result)

    def test_missing_and_wrong_credentials_are_rejected(self):
        payload = self.alarm()
        self.assertEqual(self.post("/internal/events/alarm", payload, token=False)[0], 401)
        self.assertEqual(self.post("/internal/events/alarm", payload, token="wrong")[0], 401)

    def test_invalid_contract_and_episode_mismatch_are_rejected(self):
        missing = self.alarm(); missing.pop("rule_version")
        mismatch = self.alarm(); mismatch["episode_key"] = "different"
        self.assertEqual(self.post("/internal/events/alarm", missing)[0], 422)
        self.assertEqual(self.post("/internal/events/alarm", mismatch)[0], 422)

    def test_idempotency_conflict_is_rejected(self):
        payload = self.alarm()
        self.assertEqual(self.post("/internal/events/alarm", payload)[0], 202)
        changed = {**payload, "severity": "Critical"}
        self.assertEqual(self.post("/internal/events/alarm", changed)[0], 409)


if __name__ == "__main__":
    unittest.main()
