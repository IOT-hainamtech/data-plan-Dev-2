import json
import unittest


def validate(topic, encoded):
    body = json.loads(encoded)
    parts = topic.split("/")
    if len(parts) != 6 or parts[:2] != ["nw", "v1"]:
        raise ValueError("topic")
    channel, device = parts[5], parts[4]
    required = {"schema_version", "device_id", "boot_id", "seq", "ts", "data"}
    if not required.issubset(body) or body["schema_version"] != 1 or body["device_id"] != device:
        raise ValueError("base contract")
    if channel == "telemetry":
        data, quality = body["data"], body.get("quality", "good")
        if "quality" in data:
            raise ValueError("telemetry quality location")
        if quality not in {"good", "error"} or (quality == "error" and data):
            raise ValueError("telemetry quality")
        if quality == "good" and (set(data) != {"temperature", "humidity"}
                                   or not all(isinstance(v, (int, float)) for v in data.values())):
            raise ValueError("telemetry")
    if channel == "event" and (body.get("event_type") not in {"DOOR_OPENED", "DOOR_CLOSED"}
                               or not body.get("event_id")):
        raise ValueError("event")
    if channel == "status" and body.get("state") not in {"online", "offline"}:
        raise ValueError("status")
    return body


class PayloadContractTests(unittest.TestCase):
    def test_sht31_telemetry_matches_current_ingestor(self):
        body = validate("nw/v1/demo/demo-site/TH-001/telemetry", json.dumps({
            "schema_version": 1, "device_id": "TH-001", "boot_id": "abc", "seq": 3,
            "ts": "2026-10-17T00:00:00Z",
            "quality": "good", "data": {"temperature": 30.5, "humidity": 62.0}}))
        self.assertEqual(body["quality"], "good")

    def test_sensor_error_uses_telemetry_quality(self):
        body = validate("nw/v1/demo/demo-site/TH-001/telemetry", json.dumps({
            "schema_version": 1, "device_id": "TH-001", "boot_id": "abc", "seq": 4,
            "ts": "2026-10-17T00:00:00Z", "quality": "error", "data": {}}))
        self.assertEqual(body["quality"], "error")

    def test_error_telemetry_rejects_fake_measurements(self):
        with self.assertRaisesRegex(ValueError, "quality"):
            validate("nw/v1/demo/demo-site/TH-001/telemetry", json.dumps({
                "schema_version": 1, "device_id": "TH-001", "boot_id": "abc", "seq": 5,
                "ts": "2026-10-17T00:00:00Z",
                "quality": "error", "data": {"temperature": 0, "humidity": 0}}))

    def test_quality_inside_data_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "location"):
            validate("nw/v1/demo/demo-site/TH-001/telemetry", json.dumps({
                "schema_version": 1, "device_id": "TH-001", "boot_id": "abc", "seq": 6,
                "ts": "2026-10-17T00:00:00Z",
                "data": {"quality": "good", "temperature": 30.0, "humidity": 60.0}}))

    def test_door_event_and_heartbeat(self):
        event = validate("nw/v1/demo/demo-site/DR-001/event", json.dumps({
            "schema_version": 1, "device_id": "DR-001", "boot_id": "boot", "seq": 8,
            "ts": "2026-10-17T00:00:00Z", "event_id": "DR-001-boot-8",
            "event_type": "DOOR_OPENED", "data": {"door_state": "open"}}))
        heartbeat = validate("nw/v1/demo/demo-site/DR-001/status", json.dumps({
            "schema_version": 1, "device_id": "DR-001", "boot_id": "boot", "seq": 9,
            "ts": "2026-10-17T00:01:00Z", "state": "online",
            "data": {"door_state": "open"}}))
        self.assertEqual(event["event_id"], "DR-001-boot-8")
        self.assertEqual(heartbeat["data"]["door_state"], "open")


if __name__ == "__main__":
    unittest.main()
