from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MqttProvisioningSecurityTests(unittest.TestCase):
    def test_device_acl_has_only_three_exact_publish_channels(self):
        acl = (ROOT / "mosquitto" / "acl").read_text(encoding="utf-8")
        for device in ("TH-001", "DR-001", "TH-002"):
            block = acl.split(f"user {device}\n", 1)[1].split("\nuser ", 1)[0]
            self.assertEqual(set(block.strip().splitlines()), {
                f"topic write nw/v1/demo/demo-site/{device}/telemetry",
                f"topic write nw/v1/demo/demo-site/{device}/event",
                f"topic write nw/v1/demo/demo-site/{device}/status",
            })
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
