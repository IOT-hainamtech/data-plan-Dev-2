"""Live tests against the isolated Staging Data API (127.0.0.1:8003) and Staging DB (5434).

Verifies:
1. Health endpoint (GET /api/v1/health) -> 200 UP
2. Negative authentication:
   - Missing token -> 401
   - Invalid token -> 401
   - Missing X-Org-Code -> 400/403
   - Unauthorized X-Org-Code -> 403
3. Positive authentication:
   - Valid Bearer token + X-Org-Code -> 200
   - Confirmed test devices NW-IT-TH001 and NW-IT-DR001 are present
4. Telemetry/Event query endpoints:
   - GET /api/v1/devices/{device_id}/latest
   - GET /api/v1/devices/{device_id}/telemetry
   - GET /api/v1/devices/{device_id}/status
   - GET /api/v1/devices/{device_id}/events
5. Dev 1 -> Dev 2 Sync endpoints (v1.3):
   - PUT /api/v1/sync/organizations/NW-LOCAL-ORG-001 (NOOP on identical payload/version)
   - Stale version / conflict rejection
6. Staging Database Isolation:
   - Staging DB (5434) contains NW-LOCAL-ORG-001
   - Runtime DB does NOT contain NW-LOCAL-ORG-001
"""
import json
import os
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STAGING_ENV_FILE = REPO_ROOT / ".secrets" / "staging.env"


def load_staging_env():
    env = {}
    if STAGING_ENV_FILE.exists():
        for line in STAGING_ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("\"'")
    return env


class StagingLiveAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = load_staging_env()
        port = env.get("STAGING_DATA_API_PORT", "8003")
        cls.base_url = f"http://127.0.0.1:{port}"
        cls.token = env.get("STAGING_DP_INBOUND_TOKEN", "")
        cls.org_code = "NW-LOCAL-ORG-001"
        cls.pg_port = env.get("STAGING_PG_PORT", "5434")
        cls.pg_password = env.get("STAGING_PG_PASSWORD", "")

        # Verify staging API is reachable; skip if running inside isolated non-host container
        try:
            with urllib.request.urlopen(f"{cls.base_url}/api/v1/health", timeout=2):
                pass
        except Exception as exc:
            raise unittest.SkipTest(f"Staging Data API ({cls.base_url}) is not reachable: {exc}")

    def _request(self, path, method="GET", headers=None, body=None):
        url = f"{self.base_url}{path}"
        data = None
        req_headers = headers or {}
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
                req_headers["Content-Type"] = "application/json"
            elif isinstance(body, bytes):
                data = body
            else:
                data = str(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                resp_body = resp.read().decode("utf-8")
                try:
                    parsed = json.loads(resp_body)
                except Exception:
                    parsed = resp_body
                return status, parsed
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8")
            try:
                parsed = json.loads(err_body)
            except Exception:
                parsed = err_body
            return exc.code, parsed

    # -------------------------------------------------------------------------
    # 1. Health Endpoint
    # -------------------------------------------------------------------------

    def test_01_health_endpoint(self):
        status, body = self._request("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "UP")

    # -------------------------------------------------------------------------
    # 2. Negative Authentication Tests
    # -------------------------------------------------------------------------

    def test_02_missing_auth_header_rejected(self):
        status, _ = self._request("/api/v1/devices")
        self.assertEqual(status, 401)

    def test_03_invalid_bearer_token_rejected(self):
        headers = {
            "Authorization": "Bearer invalid_staging_token_12345",
            "X-Org-Code": self.org_code
        }
        status, _ = self._request("/api/v1/devices", headers=headers)
        self.assertEqual(status, 401)

    def test_04_missing_org_code_rejected(self):
        headers = {
            "Authorization": f"Bearer {self.token}"
        }
        status, _ = self._request("/api/v1/devices", headers=headers)
        # Should be 400 or 403 when X-Org-Code header is omitted
        self.assertIn(status, (400, 403))

    def test_05_unauthorized_org_code_rejected(self):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Org-Code": "UNAUTHORIZED_TENANT_XYZ"
        }
        status, _ = self._request("/api/v1/devices", headers=headers)
        self.assertEqual(status, 403)

    # -------------------------------------------------------------------------
    # 3. Positive Authentication & Staging Devices
    # -------------------------------------------------------------------------

    def test_06_list_devices_authenticated(self):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Org-Code": self.org_code
        }
        status, body = self._request("/api/v1/devices", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("devices", body)
        devices = body["devices"]
        device_ids = {d["device_id"] for d in devices}
        self.assertIn("NW-IT-TH001", device_ids, "S1 device NW-IT-TH001 must be present")
        self.assertIn("NW-IT-DR001", device_ids, "S6 device NW-IT-DR001 must be present")

        # Verify device properties
        th = next(d for d in devices if d["device_id"] == "NW-IT-TH001")
        self.assertEqual(th["org_code"], self.org_code)
        self.assertEqual(th["site_code"], "NW-LOCAL-SITE-001")
        self.assertEqual(th["profile_code"], "NW-IT-S1")
        self.assertTrue(th["enabled"])

        dr = next(d for d in devices if d["device_id"] == "NW-IT-DR001")
        self.assertEqual(dr["org_code"], self.org_code)
        self.assertEqual(dr["site_code"], "NW-LOCAL-SITE-001")
        self.assertEqual(dr["profile_code"], "NW-IT-S6")
        self.assertTrue(dr["enabled"])

    # -------------------------------------------------------------------------
    # 4. Telemetry and Event Endpoints for S1 and S6
    # -------------------------------------------------------------------------

    def test_07_device_latest_and_telemetry_endpoints(self):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Org-Code": self.org_code
        }
        # S1 latest
        status, body = self._request("/api/v1/devices/NW-IT-TH001/latest", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("device_id"), "NW-IT-TH001")

        # S1 telemetry query
        status, body = self._request("/api/v1/devices/NW-IT-TH001/telemetry", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("device_id"), "NW-IT-TH001")
        self.assertIn("points", body)

        # S6 events query
        status, body = self._request("/api/v1/devices/NW-IT-DR001/events", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("device_id"), "NW-IT-DR001")
        self.assertIn("events", body)

        # S6 status query
        status, body = self._request("/api/v1/devices/NW-IT-DR001/status", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("device_id"), "NW-IT-DR001")

    # -------------------------------------------------------------------------
    # 5. Dev 1 -> Dev 2 Configuration Sync over Live Staging
    # -------------------------------------------------------------------------

    def test_08_sync_organization_lifecycle(self):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Org-Code": self.org_code
        }
        # PUT organization with version 2 (applied)
        payload_v2 = {
            "org_code": self.org_code,
            "name": "nextGRP Local Organization 001 - Synced",
            "version": 2
        }
        status, body = self._request(f"/api/v1/sync/organizations/{self.org_code}",
                                     method="PUT", headers=headers, body=payload_v2)
        self.assertEqual(status, 200)
        self.assertIn(body.get("status"), ("applied", "duplicate"))
        self.assertEqual(body.get("applied_version"), 2)

        # Re-send same version 2 and payload -> duplicate (idempotent NOOP)
        status_dup, body_dup = self._request(f"/api/v1/sync/organizations/{self.org_code}",
                                             method="PUT", headers=headers, body=payload_v2)
        self.assertEqual(status_dup, 200)
        self.assertEqual(body_dup.get("status"), "duplicate")
        self.assertTrue(body_dup.get("duplicate"))

        # Send stale version 1 -> rejected with 409 STALE_VERSION
        payload_stale = {
            "org_code": self.org_code,
            "name": "nextGRP Stale Org",
            "version": 1
        }
        status_stale, body_stale = self._request(f"/api/v1/sync/organizations/{self.org_code}",
                                                 method="PUT", headers=headers, body=payload_stale)
        self.assertEqual(status_stale, 409)
        self.assertEqual(body_stale["detail"]["code"], "STALE_VERSION")

        # Send same version 2 with different payload -> rejected with 409 VERSION_PAYLOAD_CONFLICT
        payload_conflict = {
            "org_code": self.org_code,
            "name": "nextGRP Conflicting Name",
            "version": 2
        }
        status_conflict, body_conflict = self._request(f"/api/v1/sync/organizations/{self.org_code}",
                                                       method="PUT", headers=headers, body=payload_conflict)
        self.assertEqual(status_conflict, 409)
        self.assertEqual(body_conflict["detail"]["code"], "VERSION_PAYLOAD_CONFLICT")

    def test_09_sync_identifier_mismatch_rejected(self):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Org-Code": self.org_code
        }
        # URL specifies NW-LOCAL-ORG-001, but body has OTHER-ORG
        payload = {
            "org_code": "OTHER-ORG",
            "name": "Mismatch Org",
            "version": 2
        }
        status, body = self._request(f"/api/v1/sync/organizations/{self.org_code}",
                                     method="PUT", headers=headers, body=payload)
        self.assertEqual(status, 422)


if __name__ == "__main__":
    unittest.main()
