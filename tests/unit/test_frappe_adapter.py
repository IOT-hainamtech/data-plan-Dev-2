import json
import unittest
import urllib.error
import urllib.parse

from integrations.nextgrp import FrappeAlarmAdapter


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._body


class RecordingOpener:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls = []

    def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if self.error:
            raise self.error
        return self.response


class FrappeAdapterTests(unittest.TestCase):
    def payload(self, kind="occurrence"):
        value = {"event_id": "event-1", "alarm_key": "alarm-1",
                 "episode_key": "alarm-1", "kind": kind}
        value["raised_at" if kind == "occurrence" else "cleared_at"] = "2026-10-29T10:00:00Z"
        return value

    def adapter(self, opener):
        return FrappeAlarmAdapter("https://nextgrp.test/api/method/receive_alarm", 5,
            "api-key:api-secret", "nextgrp.localhost", opener=opener)

    def test_request_is_form_encoded_with_token_and_site_headers(self):
        response = {"message": {"alarm_key": "alarm-1", "status": "New",
            "occurrence_count": 1, "changed": True, "duplicate": False}}
        opener = RecordingOpener(FakeResponse(200, response))
        result = self.adapter(opener).send("alarm-1", self.payload())
        request, options = opener.calls[0]
        form = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(json.loads(form["payload"][0]), self.payload())
        self.assertEqual(request.get_header("Authorization"), "token api-key:api-secret")
        self.assertEqual(request.get_header("X-frappe-site-name"), "nextgrp.localhost")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(options["timeout"], 5)
        self.assertEqual(result.body, response)

    def test_retry_reuses_identical_event_and_body(self):
        response = FakeResponse(200, {"message": {"alarm_key": "alarm-1",
            "occurrence_count": 1, "changed": False, "duplicate": True}})
        opener = RecordingOpener(response)
        adapter = self.adapter(opener)
        payload = self.payload()
        adapter.send("alarm-1", payload)
        adapter.send("alarm-1", payload)
        self.assertEqual(opener.calls[0][0].data, opener.calls[1][0].data)
        self.assertEqual(payload["event_id"], "event-1")

    def test_http_errors_are_returned_for_worker_policy(self):
        for status in (401, 403, 409, 422, 500):
            raw = json.dumps({"exc_type": "ValidationError"}).encode()
            error = urllib.error.HTTPError("https://nextgrp.test", status, "error", {}, None)
            error.read = lambda raw=raw: raw
            result = self.adapter(RecordingOpener(error=error)).send("alarm-1", self.payload())
            self.assertEqual(result.status, status)
            self.assertEqual(result.body["exc_type"], "ValidationError")

    def test_credentials_and_https_are_fail_closed(self):
        with self.assertRaises(ValueError):
            FrappeAlarmAdapter("http://nextgrp.test", 5, "key:secret", "site")
        with self.assertRaises(ValueError):
            FrappeAlarmAdapter("https://nextgrp.test", 5, " bad-token:secret", "site")
        with self.assertRaises(ValueError):
            FrappeAlarmAdapter("https://nextgrp.test", 5, "key:secret", "")

    def test_single_api_key_without_secret_is_rejected(self):
        # Must fail closed if secret is missing (correcting Codex bug)
        with self.assertRaises(ValueError):
            FrappeAlarmAdapter("https://nextgrp.test", 5, "api-key-only", "site")

    def test_api_key_and_secret_pair_is_supported(self):
        adapter = FrappeAlarmAdapter("https://nextgrp.test", 5, api_key="my-key",
                                     api_secret="my-secret", site_name="site")
        self.assertEqual(adapter.token, "my-key:my-secret")
        self.assertEqual(adapter.api_key, "my-key")
        self.assertEqual(adapter.api_secret, "my-secret")

    def test_validate_ack_occurrence_and_duplicate(self):
        occurrence_body = {"message": {"alarm_key": "alarm-1", "status": "New",
                                       "occurrence_count": 1, "changed": True, "duplicate": False}}
        self.assertTrue(FrappeAlarmAdapter.validate_ack(occurrence_body, "alarm-1", "evt-1"))

        dup_body = {"message": {"alarm_key": "alarm-1", "occurrence_count": 1,
                                "changed": False, "duplicate": True}}
        self.assertTrue(FrappeAlarmAdapter.validate_ack(dup_body, "alarm-1", "evt-1"))

        # Inconsistent duplicate with changed=True
        invalid_dup = {"message": {"alarm_key": "alarm-1", "occurrence_count": 1,
                                   "changed": True, "duplicate": True}}
        self.assertFalse(FrappeAlarmAdapter.validate_ack(invalid_dup, "alarm-1", "evt-1"))

        # Mismatch alarm_key
        self.assertFalse(FrappeAlarmAdapter.validate_ack(occurrence_body, "wrong-key", "evt-1"))

        # Missing or non-dict message
        self.assertFalse(FrappeAlarmAdapter.validate_ack({}, "alarm-1", "evt-1"))
        self.assertFalse(FrappeAlarmAdapter.validate_ack({"message": "string"}, "alarm-1", "evt-1"))

    def test_delivery_receipt_preserves_event_id(self):
        receipt = FrappeAlarmAdapter.delivery_receipt({"message": {}}, "alarm-1", "evt-100")
        self.assertEqual(receipt, "evt-100")


if __name__ == "__main__":
    unittest.main()
