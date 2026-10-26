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


if __name__ == "__main__":
    unittest.main()
