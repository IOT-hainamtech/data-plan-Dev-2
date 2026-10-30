"""Static security assertions for deployment defaults."""
from pathlib import Path
import unittest


class ComposeSecurityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
