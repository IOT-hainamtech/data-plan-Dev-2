import csv
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProvisioningContractTests(unittest.TestCase):
    def load(self, name):
        with (ROOT / "config" / name).open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        return {row["key"]: row for row in rows if row["key"] != "nwcfg"}

    def test_th001_template_matches_firmware_and_broker_contract(self):
        values = self.load("nvs.th001.template.csv")
        self.assertEqual(values["device_id"]["value"], "TH-001")
        self.assertEqual(values["mqtt_username"]["value"], "TH-001")
        self.assertEqual(values["sensor_type"]["value"], "sht31")
        self.assertEqual(values["profile_code"]["value"], "TEMP_HUM_SENSOR")
        self.assertEqual(values["mqtt_uri"]["value"], "mqtts://192.168.1.40:8883")
        self.assertEqual(values["sht31_address"]["value"], "68")
        self.assertEqual(values["telemetry_sec"]["value"], "60")

    def test_dr001_template_matches_firmware_and_broker_contract(self):
        values = self.load("nvs.dr001.template.csv")
        self.assertEqual(values["device_id"]["value"], "DR-001")
        self.assertEqual(values["mqtt_username"]["value"], "DR-001")
        self.assertEqual(values["sensor_type"]["value"], "reed_door")
        self.assertEqual(values["profile_code"]["value"], "DOOR_SENSOR")
        self.assertEqual(values["mqtt_uri"]["value"], "mqtts://192.168.1.40:8883")
        self.assertEqual(values["door_closed_lvl"]["value"], "0")
        self.assertEqual(values["door_debounce"]["value"], "50")
        self.assertEqual(values["heartbeat_sec"]["value"], "60")

    def test_templates_contain_placeholders_not_secrets(self):
        for name in ("nvs.th001.template.csv", "nvs.dr001.template.csv"):
            text = (ROOT / "config" / name).read_text(encoding="utf-8")
            self.assertIn("__WIFI_PASSWORD__", text)
            self.assertIn("__MQTT_PASSWORD__", text)
            self.assertNotIn("BEGIN PRIVATE KEY", text)

    def test_nvs_partition_is_0x6000_at_0x9000(self):
        with (ROOT / "partitions.csv").open(encoding="utf-8") as source:
            rows = list(csv.reader(source))
        nvs = next(row for row in rows if row and row[0].strip() == "nvs")
        self.assertEqual(nvs[3].strip(), "0x9000")
        self.assertEqual(nvs[4].strip(), "0x6000")


if __name__ == "__main__":
    unittest.main()
