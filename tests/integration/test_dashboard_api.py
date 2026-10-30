"""Read-only dashboard API contract tests against the running Data API."""
import json
import os
import unittest
import urllib.error
import urllib.parse
import urllib.request


@unittest.skipUnless(os.getenv("RUN_DASHBOARD_TESTS") == "1", "set RUN_DASHBOARD_TESTS=1")
class DashboardApiTests(unittest.TestCase):
    base = os.getenv("DATA_API_URL", "http://data-api:8000")
    token = os.getenv("DP_TEST_TOKEN")

    def get(self, path, *, org="demo", token=True):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {self.token}"
        if org is not None:
            headers["X-Org-Code"] = org
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_auth_tenant_and_device_pagination(self):
        self.assertEqual(self.get("/api/v1/devices", token=False)[0], 401)
        self.assertEqual(self.get("/api/v1/devices", org="not-allowed")[0], 403)
        status, body = self.get("/api/v1/devices?limit=1&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual((body["limit"], body["offset"], len(body["devices"])), (1, 0, 1))
        self.assertGreaterEqual(body["total"], 1)
        self.assertIn("last_seen_at", body["devices"][0])

    def test_outbox_metrics_requires_auth_and_tenant_scope(self):
        self.assertEqual(self.get("/api/v1/outbox/metrics", token=False)[0], 401)
        self.assertEqual(self.get("/api/v1/outbox/metrics", org=None)[0], 400)
        self.assertEqual(self.get("/api/v1/outbox/metrics", org="not-allowed")[0], 403)
        status, body = self.get("/api/v1/outbox/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {
            "pending", "failed", "delivery_failures", "oldest_pending_age_seconds"
        })
        self.assertTrue(all(body[name] >= 0 for name in body))

    def test_s1_latest_and_history(self):
        status, body = self.get("/api/v1/devices/TH-001/latest")
        self.assertEqual(status, 200)
        metrics = {item["metric"]: item for item in body["metrics"]}
        self.assertIn("temperature", metrics)
        self.assertIn("humidity", metrics)
        self.assertIn("measured_at", metrics["temperature"])
        self.assertEqual(metrics["temperature"]["unit"], "Cel")
        self.assertEqual(metrics["humidity"]["unit"], "%RH")
        self.assertIn(body["quality"], ("good", "error"))
        self.assertIn(body["data_status"], ("LIVE", "STALE", "ERROR"))
        self.assertEqual(body["timezone"], "Asia/Ho_Chi_Minh")
        self.assertIn("measured_at_utc", body)
        self.assertIn("measured_at_local", body)
        self.assertIn("age_seconds", body)
        status, history = self.get("/api/v1/devices/TH-001/telemetry?metric=temperature&limit=2&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual((history["limit"], history["offset"]), (2, 0))
        self.assertLessEqual(len(history["points"]), 2)
        self.assertTrue(all(point["metric"] == "temperature" for point in history["points"]))
        self.assertTrue(all(point["unit"] == "Cel" for point in history["points"]))
        self.assertTrue(all("quality" in point and "measured_at_local" in point
                            for point in history["points"]))

    def test_s6_events_and_latest_door_state(self):
        status, body = self.get("/api/v1/devices/DR-001/events?limit=1&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["events"]), 1)
        event = body["events"][0]
        self.assertIn(event["event_type"], ("DOOR_OPENED", "DOOR_CLOSED"))
        self.assertIn("door_state", event["payload"]["data"])
        status, latest = self.get("/api/v1/devices/DR-001/latest")
        self.assertEqual(status, 200)
        self.assertIn(latest["door_state"], ("open", "closed"))
        self.assertIn(latest["latest_snapshot"]["door_state"], ("open", "closed"))
        self.assertEqual(latest["timezone"], "Asia/Ho_Chi_Minh")
        self.assertIn("received_at_local", latest["latest_snapshot"])

    def test_device_status_and_alarm_projection(self):
        status, body = self.get("/api/v1/devices/TH-001/status?history_limit=2")
        self.assertEqual(status, 200)
        self.assertIn(body["current_state"], ("online", "offline"))
        self.assertIn("last_seen_at", body)
        status, alarms = self.get("/api/v1/alarms?limit=2&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual((alarms["limit"], alarms["offset"]), (2, 0))
        for alarm in alarms["alarms"]:
            for key in ("alarm_key", "severity", "status", "summary", "raised_at"):
                self.assertIn(key, alarm)


if __name__ == "__main__":
    unittest.main()
