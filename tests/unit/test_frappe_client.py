import io
import json
import unittest
import urllib.error
import urllib.parse

from integrations.frappe_client import (METHOD_PREFIX, FrappeClient,
                                         FrappeHTTPError,
                                         validate_and_normalize_credentials)


class Response:
    def __init__(self, status, body):
        self.status = status
        self._raw = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        return self._raw


class Recorder:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def __call__(self, request, **options):
        self.requests.append((request, options))
        if self.error:
            raise self.error
        return self.response


class FrappeClientTests(unittest.TestCase):
    def client(self, recorder, token="api-key:api-secret", **kwargs):
        return FrappeClient("http://192.168.1.43:8000", "nextgrp.localhost", token=token,
                            allow_http_lan=True, opener=recorder, **kwargs)

    # -------------------------------------------------------------------------
    # Authentication & Credential Tests
    # -------------------------------------------------------------------------

    def test_credentials_require_both_key_and_secret(self):
        # Combined string format
        k, s, token = validate_and_normalize_credentials("my-key:my-secret")
        self.assertEqual((k, s, token), ("my-key", "my-secret", "my-key:my-secret"))

        # Keyword arguments format
        k, s, token = validate_and_normalize_credentials(api_key="my-key", api_secret="my-secret")
        self.assertEqual((k, s, token), ("my-key", "my-secret", "my-key:my-secret"))

        # Rejects single key without secret (the bug Codex had)
        with self.assertRaises(ValueError) as ctx:
            validate_and_normalize_credentials("api-key-only")
        self.assertIn("API_KEY and API_SECRET separated by ':'", str(ctx.exception))
        # Ensure secret is not echoed
        self.assertNotIn("api-key-only", str(ctx.exception))

        # Rejects missing secret or missing key
        with self.assertRaises(ValueError):
            validate_and_normalize_credentials("key:")
        with self.assertRaises(ValueError):
            validate_and_normalize_credentials(":secret")
        with self.assertRaises(ValueError):
            validate_and_normalize_credentials(api_key="key", api_secret="")
        with self.assertRaises(ValueError):
            validate_and_normalize_credentials(api_key="", api_secret="secret")

        # Rejects newlines / carriage returns
        with self.assertRaises(ValueError):
            validate_and_normalize_credentials("key\n:secret")
        with self.assertRaises(ValueError):
            validate_and_normalize_credentials("key:sec\rret")

        # Rejects empty or None
        with self.assertRaises(ValueError):
            validate_and_normalize_credentials("")
        with self.assertRaises(ValueError):
            validate_and_normalize_credentials(None)

    def test_client_repr_does_not_leak_credentials(self):
        client = FrappeClient("https://dev1.example.com", "nextgrp.localhost",
                              token="super-secret-key:super-secret-pass")
        text = repr(client)
        self.assertNotIn("super-secret-key", text)
        self.assertNotIn("super-secret-pass", text)

    # -------------------------------------------------------------------------
    # GET Device Configuration
    # -------------------------------------------------------------------------

    def test_get_config_uses_confirmed_headers_and_message_envelope(self):
        recorder = Recorder(Response(200, {"message": {"device": {"device_id": "NW-IT-TH001"}}}))
        response = self.client(recorder).get_device_config("NW-IT-TH001")
        request, _ = recorder.requests[0]
        self.assertIn(METHOD_PREFIX + "config_controller.get_device_config", request.full_url)
        self.assertIn("device_id=NW-IT-TH001", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "token api-key:api-secret")
        self.assertEqual(request.get_header("X-frappe-site-name"), "nextgrp.localhost")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(response.message["device"]["device_id"], "NW-IT-TH001")

    # -------------------------------------------------------------------------
    # POST Status
    # -------------------------------------------------------------------------

    def test_post_status_uses_form_encoded_device_id_and_state(self):
        recorder = Recorder(Response(200, {"message": {"device_id": "NW-IT-TH001", "online_state": "Online"}}))
        self.client(recorder).post_status("NW-IT-TH001", "online")
        request, _ = recorder.requests[-1]
        self.assertIn(METHOD_PREFIX + "status_controller.receive_status", request.full_url)
        form = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(form["device_id"], ["NW-IT-TH001"])
        self.assertEqual(form["state"], ["online"])
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(request.get_header("Authorization"), "token api-key:api-secret")

    def test_post_status_validates_state(self):
        client = self.client(Recorder())
        with self.assertRaisesRegex(ValueError, "state must be 'online' or 'offline'"):
            client.post_status("NW-IT-TH001", "invalid_state")

    # -------------------------------------------------------------------------
    # POST Alarm
    # -------------------------------------------------------------------------

    def test_alarm_is_form_encoded_payload_json(self):
        recorder = Recorder(Response(200, {"message": {"accepted": True}}))
        payload = {
            "event_id": "evt-1",
            "org_code": "NW-ORG",
            "device_id": "NW-IT-TH001",
            "alarm_key": "rule:dev:1",
            "kind": "occurrence"
        }
        self.client(recorder).post_alarm(payload)
        request, _ = recorder.requests[-1]
        self.assertIn(METHOD_PREFIX + "alarm_controller.receive_alarm", request.full_url)
        parsed = json.loads(urllib.parse.parse_qs(request.data.decode())["payload"][0])
        self.assertEqual(parsed["device_id"], "NW-IT-TH001")
        self.assertEqual(parsed["event_id"], "evt-1")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")

    def test_alarm_retry_retains_identical_payload(self):
        recorder = Recorder(Response(200, {"message": {"duplicate": True}}))
        payload = {
            "event_id": "evt-1",
            "org_code": "NW-ORG",
            "device_id": "NW-IT-TH001",
            "alarm_key": "rule:dev:1",
            "kind": "occurrence"
        }
        client = self.client(recorder)
        client.post_alarm(payload)
        client.post_alarm(payload)
        self.assertEqual(recorder.requests[0][0].data, recorder.requests[1][0].data)

    # -------------------------------------------------------------------------
    # Command Endpoints (Claim / ACK)
    # -------------------------------------------------------------------------

    def test_claim_command_uses_form_encoded_device_id(self):
        recorder = Recorder(Response(200, {"message": {"command": {"command_id": "CMD-001"}}}))
        response = self.client(recorder).claim_command("NW-IT-TH001")
        request, _ = recorder.requests[-1]
        self.assertIn(METHOD_PREFIX + "command_controller.claim_command", request.full_url)
        form = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(form["device_id"], ["NW-IT-TH001"])
        self.assertEqual(response.message["command"]["command_id"], "CMD-001")

    def test_ack_command_uses_form_encoded_fields(self):
        recorder = Recorder(Response(200, {"message": {"command_id": "CMD-001", "status": "Succeeded", "duplicate": False}}))
        response = self.client(recorder).ack_command("CMD-001", "token-xyz", "Succeeded", result_json={"ack": True})
        request, _ = recorder.requests[-1]
        self.assertIn(METHOD_PREFIX + "command_controller.ack_command", request.full_url)
        form = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(form["command_id"], ["CMD-001"])
        self.assertEqual(form["delivery_token"], ["token-xyz"])
        self.assertEqual(form["status"], ["Succeeded"])
        self.assertEqual(json.loads(form["result_json"][0]), {"ack": True})
        self.assertEqual(response.message["status"], "Succeeded")

    def test_ack_command_validates_status(self):
        client = self.client(Recorder())
        with self.assertRaisesRegex(ValueError, "status must be 'Succeeded' or 'Failed'"):
            client.ack_command("CMD-001", "token-xyz", "UnknownStatus")

    # -------------------------------------------------------------------------
    # Error Handling & Fail-Closed Policy
    # -------------------------------------------------------------------------

    def test_error_policy_and_envelope_are_fail_closed(self):
        for status, retryable in ((401, False), (403, False), (404, False), (409, False),
                                  (422, False), (429, True), (500, True), (502, True), (503, True)):
            error = urllib.error.HTTPError("http://dev1", status, "error", {}, None)
            error.read = lambda: json.dumps({"message": {"error": "rejected"}}).encode()
            with self.assertRaises(FrappeHTTPError) as caught:
                self.client(Recorder(error=error)).get_device_config("NW-IT-TH001")
            self.assertEqual(caught.exception.retryable, retryable)
        with self.assertRaisesRegex(ValueError, "missing message"):
            self.client(Recorder(Response(200, {"data": {}}))).get_device_config("NW-IT-TH001")

    def test_http_502_html_is_handled_cleanly_as_retryable_error(self):
        error = urllib.error.HTTPError("http://dev1", 502, "Bad Gateway", {}, None)
        error.read = lambda: b"<html><body>502 Bad Gateway</body></html>"
        with self.assertRaises(FrappeHTTPError) as caught:
            self.client(Recorder(error=error)).get_device_config("NW-IT-TH001")
        self.assertEqual(caught.exception.status, 502)
        self.assertTrue(caught.exception.retryable)

    def test_http_requires_explicit_safe_configuration(self):
        with self.assertRaises(ValueError):
            FrappeClient("http://192.168.1.43:8000", "site", "key:secret")
        with self.assertRaises(ValueError):
            FrappeClient("https://dev1", "site", " key:secret")


if __name__ == "__main__":
    unittest.main()
