from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class OrgSiteCutoverSafetyTests(unittest.TestCase):
    def test_alarm_api_uses_immutable_occurrence_org(self):
        source = (ROOT / "app" / "api" / "application.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("a.payload_json->>'org_code'=%s"), 3)
        self.assertNotIn("JOIN devices d ON d.device_id=a.device_id AND d.org_code=%s", source)
        self.assertNotIn("JOIN devices d ON d.device_id=a.device_id\n                WHERE d.org_code=%s", source)

    def test_manual_migration_is_not_auto_mounted(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("./db/migrations:/docker-entrypoint-initdb.d:ro", compose)
        self.assertNotIn("./db/manual:/docker-entrypoint-initdb.d", compose)


if __name__ == "__main__":
    unittest.main()
