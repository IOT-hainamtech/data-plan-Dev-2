import io
import json
import unittest
import urllib.error

from tests.e2e.dev1_dev2.data_api_read_client import read_latest


class _Response:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return json.dumps(self.body).encode()


class DataApiReadClientTests(unittest.TestCase):
    def test_uses_bearer_internal_tenant_and_maps_response(self):
        seen = {}
        def open_request(request, **_):
            seen["request"] = request
            return _Response({"organization": "hainam", "device_id": "TH-001",
                              "online_status": "online", "data_status": "LIVE", "seq": 9})
        result = read_latest("http://127.0.0.1:8002", "secret-value",
                             "NW-IT-TH001", opener=open_request)
        request = seen["request"]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-value")
        self.assertEqual(request.get_header("X-org-code"), "hainam")
        self.assertTrue(request.full_url.endswith("/api/v1/devices/TH-001/latest"))
        self.assertEqual(result["organization"], "NW-LOCAL-ORG-001")
        self.assertEqual(result["device_id"], "NW-IT-TH001")

    def test_door_timestamp_is_projected_from_latest_snapshot(self):
        def open_request(*_args, **_kwargs):
            return _Response({"organization": "hainam", "device_id": "DR-001",
                "online_status": "online", "data_status": "LIVE", "seq": 10,
                "latest_snapshot": {"measured_at_utc": "2026-11-02T03:00:00Z",
                                    "received_at_utc": "2026-11-02T03:00:01Z"},
                "door_state": "closed"})
        result = read_latest("http://127.0.0.1:8002", "secret", "NW-IT-DR001",
                             opener=open_request)
        self.assertEqual(result["measured_at_utc"], "2026-11-02T03:00:00Z")

    def test_unapproved_device_fails_before_http(self):
        with self.assertRaisesRegex(ValueError, "approved integration mapping"):
            read_latest("http://127.0.0.1:8002", "secret", "UNKNOWN")

    def test_401_explains_bearer_is_distinct_from_frappe_token(self):
        def unauthorized(request, **_):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO())
        with self.assertRaisesRegex(RuntimeError, "dedicated Bearer token"):
            read_latest("http://127.0.0.1:8002", "wrong", "NW-IT-DR001", opener=unauthorized)


if __name__ == "__main__":
    unittest.main()
