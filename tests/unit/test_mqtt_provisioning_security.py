from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MqttProvisioningSecurityTests(unittest.TestCase):
    def test_device_acl_has_only_exact_publish_channels_during_cutover(self):
        acl = (ROOT / "mosquitto" / "acl").read_text(encoding="utf-8")
        routes = {"TH-001": [("hainam", "hq"), ("NW-LOCAL-ORG-001", "NW-LOCAL-SITE-001")],
                  "DR-001": [("hainam", "hq"), ("NW-LOCAL-ORG-001", "NW-LOCAL-SITE-001")],
                  "TH-002": [("demo", "demo-site")]}
        for device, locations in routes.items():
            block = acl.split(f"user {device}\n", 1)[1].split("\nuser ", 1)[0]
            expected = {f"topic write nw/v1/{org}/{site}/{device}/{channel}"
                        for org, site in locations
                        for channel in ("telemetry", "event", "status")}
            self.assertEqual(set(block.strip().splitlines()), expected)
            self.assertNotIn("/#", block)
            self.assertNotIn("topic read", block)

    def test_account_tool_generates_unique_device_credential_without_printing_it(self):
        script = (ROOT / "scripts" / "manage-mqtt-account.ps1").read_text(encoding="utf-8")
        self.assertIn("RandomNumberGenerator]::Fill", script)
        self.assertIn("$plain.Length -lt 16", script)
        self.assertIn("$Username -ne $DeviceId", script)
        self.assertIn("ConvertFrom-SecureString | Set-Content", script)
        self.assertNotIn('Write-Host $plain', script)
        self.assertNotIn('$DeviceId/#', script)


if __name__ == "__main__":
    unittest.main()
