"""Static security assertions for deployment defaults."""
from pathlib import Path
import unittest


class ComposeSecurityTests(unittest.TestCase):
    def test_fast_presence_is_disabled_by_default(self):
        compose = (Path(__file__).resolve().parents[2] / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("FAST_PRESENCE_DEVICE_IDS: ${FAST_PRESENCE_DEVICE_IDS:-}", compose)
        self.assertIn("FAST_PRESENCE_TIMEOUT_SECONDS: ${FAST_PRESENCE_TIMEOUT_SECONDS:-4}", compose)
        self.assertIn("FAST_PRESENCE_RECONCILE_SECONDS: ${FAST_PRESENCE_RECONCILE_SECONDS:-0.5}", compose)

    def test_data_api_requires_token_and_defaults_to_loopback(self):
        compose = (Path(__file__).resolve().parents[2] / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn(
            "DP_INBOUND_TOKEN: ${DP_INBOUND_TOKEN:?Set DP_INBOUND_TOKEN to a private service token}",
            compose,
        )
        self.assertIn('${DATA_API_BIND_IP:-127.0.0.1}:8002:8000', compose)
        self.assertNotIn("mock-data-plane-inbound-token", compose)

    def test_outbound_delivery_is_profile_gated_and_disabled_by_default(self):
        compose = (Path(__file__).resolve().parents[2] / "compose.yaml").read_text(encoding="utf-8")
        worker = compose.split("  outbox-worker:", 1)[1].split("  snapshot-worker:", 1)[0]
        self.assertIn('profiles: ["outbound"]', worker)
        self.assertIn("OUTBOX_DELIVERY_ENABLED: ${OUTBOX_DELIVERY_ENABLED:-false}", worker)

    def test_non_root_data_plane_uses_dedicated_ca_only_volume(self):
        root = Path(__file__).resolve().parents[2]
        compose = (root / "compose.yaml").read_text(encoding="utf-8")
        dockerfile = (root / "app" / "Dockerfile").read_text(encoding="utf-8")
        data_plane = compose.split("  data-plane:", 1)[1].split("  data-api:", 1)[0]
        ca_init = compose.split("  mqtt-ca-init:", 1)[1].split("  postgres:", 1)[0]
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("mqtt_ca:/run/mqtt:ro", data_plane)
        self.assertNotIn("certs/ca.crt:/run/mqtt", data_plane)
        self.assertIn("./mosquitto/certs/ca.crt:/source/ca.crt:ro", ca_init)
        self.assertNotIn("server.key", ca_init)
        self.assertIn("chmod 0444 /target/ca.crt", ca_init)


if __name__ == "__main__":
    unittest.main()
