import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MqttReconnectContractTests(unittest.TestCase):
    def test_esp_mqtt_is_the_only_reconnect_owner(self):
        source = (ROOT / "src" / "mqtt_manager.cpp").read_text(encoding="utf-8")
        self.assertIn("cfg.network.disable_auto_reconnect = false", source)
        self.assertNotIn("esp_mqtt_client_reconnect", source)
        self.assertNotIn("reconnect_task", source)

    def test_device_id_is_the_stable_client_id(self):
        source = (ROOT / "src" / "mqtt_manager.cpp").read_text(encoding="utf-8")
        self.assertIn("cfg.credentials.client_id = c.device_id.c_str()", source)

    def test_disconnect_releases_old_session_tracker(self):
        source = (ROOT / "src" / "mqtt_manager.cpp").read_text(encoding="utf-8")
        disconnected = source.split("MQTT_EVENT_DISCONNECTED", 1)[1].split(
            "MQTT_EVENT_PUBLISHED", 1)[0]
        self.assertIn("inflight_.clear()", disconnected)

    def test_presence_is_never_stored_for_replay(self):
        source = (ROOT / "src" / "main.cpp").read_text(encoding="utf-8")
        self.assertIn("pending_presence_seq, false, false, 1", source)

    def test_presence_has_a_reliable_lane_without_business_inflight_slots(self):
        source = (ROOT / "src" / "mqtt_manager.cpp").read_text(encoding="utf-8")
        presence_lane = source.split("if (!durable)", 1)[1].split(
            "xSemaphoreTake", 1)[0]
        self.assertIn("esp_mqtt_client_enqueue", presence_lane)
        self.assertNotIn("inflight_", presence_lane)
        self.assertIn("qos, retain, durable", source)


if __name__ == "__main__":
    unittest.main()
