import io
import json
import unittest
from unittest.mock import patch

from integrations.dev1_config import (Dev1ConfigClient, map_alarm_payload,
                                      reconcile_device_config,
                                      resolve_confirmed_mapping,
                                      resolve_internal_alarm_key)


class Response(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class Cursor:
    def __init__(self, row): self.row = row; self.args = None
    def execute(self, sql, args): self.args = args
    def fetchone(self): return self.row


class MappingCursor:
    def __init__(self, rows):
        self.rows = rows
        self.key = None
    def execute(self, sql, args):
        self.key = (args[1], args[2]) if len(args) >= 3 else None
    def fetchone(self):
        return self.rows.get(self.key)


class ReverseCursor:
    def __init__(self):
        self.row = None
        self.mappings = {
            ("ORGANIZATION", "NW-LOCAL-ORG-001"): ("demo", 1),
            ("SITE", "NW-LOCAL-SITE-001"): ("demo-site", 1),
            ("DEVICE", "NW-LOCAL-TH001"): ("TH-001", 1),
            ("PROFILE", "NW-LOCAL-S1"): ("TEMP_HUM_SENSOR", 1),
            ("RULE", "NW-LOCAL-RULE-S1"): ("S1_TEMP_HIGH", 1),
            ("DEVICE", "NW-LOCAL-DR001"): ("DR-001", 1),
            ("PROFILE", "NW-LOCAL-S6"): ("DOOR_SENSOR", 1),
            ("RULE", "NW-LOCAL-RULE-S6"): ("S6_DOOR_OPEN", 1),
        }
    def execute(self, sql, args):
        if "external_id=%s" in sql:
            self.row = self.mappings.get((args[1], args[2]))
        elif "SELECT zone_code" in sql:
            self.row = ("LAB",)
    def fetchone(self): return self.row


class AlarmKeyCursor:
    def __init__(self, direct=None, outbound=()):
        self.direct, self.outbound, self.query = direct, outbound, ""
    def execute(self, sql, args): self.query = sql
    def fetchone(self): return (self.direct,) if self.direct else None
    def fetchall(self): return [(value,) for value in self.outbound]


class Dev1ConfigTests(unittest.TestCase):
    def test_fetch_uses_actual_frappe_shape_and_auth(self):
        seen = {}
        body = {"message": {"device": {"device_id": "NW-LOCAL-TH001",
            "config_version": 1}, "profile": {}, "rules": []}}
        def opener(request, **kwargs):
            seen["request"] = request
            return Response(json.dumps(body).encode())
        client = Dev1ConfigClient("http://192.168.1.43:8000/api/method/get_device_config",
            2, "key:secret", "nextgrp.local", allow_insecure_lab=True, opener=opener)
        self.assertEqual(client.get_device_config("NW-LOCAL-TH001")["device"]["config_version"], 1)
        self.assertIn("device_id=NW-LOCAL-TH001", seen["request"].full_url)
        self.assertEqual(seen["request"].get_header("Authorization"), "token key:secret")

    def test_https_is_default_and_response_identity_is_strict(self):
        with self.assertRaises(ValueError):
            Dev1ConfigClient("http://example.test/config", 2, "key:secret", "site")
        wrong = {"message": {"device": {"device_id": "OTHER", "config_version": 1},
                             "profile": {}, "rules": []}}
        client = Dev1ConfigClient("https://example.test/config", 2, "key:secret", "site",
            opener=lambda *a, **k: Response(json.dumps(wrong).encode()))
        with self.assertRaisesRegex(ValueError, "does not match"):
            client.get_device_config("NW-LOCAL-TH001")

    def test_mapping_must_be_confirmed(self):
        self.assertEqual(resolve_confirmed_mapping(Cursor(("NW-LOCAL-TH001", 1)),
            "DEVICE", "TH-001"), ("NW-LOCAL-TH001", 1))
        with self.assertRaises(LookupError):
            resolve_confirmed_mapping(Cursor(None), "DEVICE", "DR-001")

    def test_alarm_mapping_uses_confirmed_ids_and_rule_version(self):
        cur = MappingCursor({
            ("ORGANIZATION", "demo"): ("NW-LOCAL", 1),
            ("DEVICE", "TH-001"): ("NW-LOCAL-TH001", 1),
            ("RULE", "S1_TEMP_HIGH"): ("NW-LOCAL-RULE-S1", 1),
            ("ZONE", "LAB"): ("NW-LOCAL-LAB", 1),
        })
        mapped = map_alarm_payload(cur, {
            "org_code": "demo", "device_id": "TH-001", "rule_id": "S1_TEMP_HIGH",
            "rule_version": 99, "zone_code": "LAB",
            "episode_start": "2026-10-30T03:00:00Z", "alarm_key": "internal",
            "episode_key": "internal",
        })
        self.assertEqual(mapped["device_id"], "NW-LOCAL-TH001")
        self.assertEqual(mapped["rule_id"], "NW-LOCAL-RULE-S1")
        self.assertEqual(mapped["rule_version"], 1)
        self.assertEqual(mapped["alarm_key"],
                         "NW-LOCAL-RULE-S1:NW-LOCAL-TH001:1793329200")
        self.assertEqual(mapped["episode_key"], mapped["alarm_key"])

    def test_external_callback_key_resolves_only_from_frozen_outbound_mapping(self):
        self.assertEqual(resolve_internal_alarm_key(
            AlarmKeyCursor(outbound=("S1_TEMP_HIGH:TH-001:1",)),
            "NW-LOCAL-RULE-S1:NW-LOCAL-TH001:1"), "S1_TEMP_HIGH:TH-001:1")
        with self.assertRaises(LookupError):
            resolve_internal_alarm_key(AlarmKeyCursor(), "unknown")
        with self.assertRaisesRegex(ValueError, "multiple"):
            resolve_internal_alarm_key(AlarmKeyCursor(outbound=("internal-a", "internal-b")),
                                       "ambiguous")

    def test_alarm_mapping_never_guesses_unconfirmed_s6_device(self):
        cur = MappingCursor({
            ("ORGANIZATION", "demo"): ("NW-LOCAL", 1),
        })
        with self.assertRaisesRegex(LookupError, "DEVICE"):
            map_alarm_payload(cur, {"org_code": "demo", "device_id": "DR-001",
                "rule_id": "S6_DOOR_OPEN", "episode_start": "2026-10-30T03:00:00Z"})

    def test_actual_device_config_is_mapped_and_applied_as_one_unit(self):
        config = {"device": {"device_id": "NW-LOCAL-TH001", "device_profile": "NW-LOCAL-S1",
            "site": "NW-LOCAL-SITE-001", "zone": None, "status": "Active",
            "connectivity_type": "WIFI", "config_version": 1},
            "site": {"organization": "NW-LOCAL-ORG-001"},
            "profile": {"profile_code": "NW-LOCAL-S1", "version": 1, "protocol": "MQTT",
                "supports_telemetry": 1, "data_points": [
                    {"code": "temperature", "unit": "Cel"},
                    {"code": "humidity", "unit": "%RH"}], "event_types": []},
            "rules": [{"rule_id": "NW-LOCAL-RULE-S1", "rule_type": "THRESHOLD",
                "scope_type": "Device", "scope_ref": "NW-LOCAL-TH001",
                "data_point": "temperature", "operator": ">", "threshold": 30.0,
                "severity": "High", "active": 1, "version": 1, "auto_close": 1}]}
        with patch("integrations.dev1_config.apply_profile", side_effect=lambda c, d: ("profile", d)), \
             patch("integrations.dev1_config.apply_device", side_effect=lambda c, d: ("device", d)), \
             patch("integrations.dev1_config.apply_rule", side_effect=lambda c, d: ("rule", d)):
            results = reconcile_device_config(ReverseCursor(), config)
        self.assertEqual([item[0] for item in results], ["profile", "device", "rule"])
        self.assertEqual(results[0][1]["profile_code"], "TEMP_HUM_SENSOR")
        self.assertEqual(results[1][1]["device_id"], "TH-001")
        self.assertEqual(results[2][1]["rule_id"], "S1_TEMP_HIGH")
        self.assertEqual(results[2][1]["version"], 1)

    def test_s6_schedule_is_mapped_without_inventing_defaults(self):
        config = {"device": {"device_id": "NW-LOCAL-DR001", "device_profile": "NW-LOCAL-S6",
            "site": "NW-LOCAL-SITE-001", "zone": None, "status": "Active",
            "connectivity_type": "WIFI", "config_version": 2},
            "site": {"organization": "NW-LOCAL-ORG-001"},
            "profile": {"profile_code": "NW-LOCAL-S6", "version": 2, "protocol": "MQTT",
                "supports_telemetry": 1, "data_points": [{"code": "door_state",
                "data_type": "string", "allowed_values": ["open", "closed"]}],
                "event_types": ["DOOR_OPENED", "DOOR_CLOSED"]},
            "rules": [{"rule_id": "NW-LOCAL-RULE-S6", "rule_type": "STATE_CHANGE",
                "scope_type": "Device", "scope_ref": "NW-LOCAL-DR001",
                "event_type": "DOOR_OPENED", "open_limit_s": 300,
                "business_start": "08:00:00", "business_end": "18:00:00",
                "schedule_timezone": "Asia/Ho_Chi_Minh", "severity": "High",
                "active": 1, "version": 3}]}
        with patch("integrations.dev1_config.apply_profile", side_effect=lambda c, d: d), \
             patch("integrations.dev1_config.apply_device", side_effect=lambda c, d: d), \
             patch("integrations.dev1_config.apply_rule", side_effect=lambda c, d: d):
            rule = reconcile_device_config(ReverseCursor(), config)[2]
        self.assertEqual((rule["business_start"], rule["business_end"], rule["timezone"]),
                         ("08:00:00", "18:00:00", "Asia/Ho_Chi_Minh"))

    def test_s6_missing_schedule_and_profile_contradiction_fail_closed(self):
        base = {"device": {"device_id": "NW-LOCAL-DR001", "device_profile": "NW-LOCAL-S6",
            "site": "NW-LOCAL-SITE-001", "zone": None, "status": "Active",
            "connectivity_type": "WIFI", "config_version": 2},
            "site": {"organization": "NW-LOCAL-ORG-001"},
            "profile": {"profile_code": "NW-LOCAL-S6", "version": 2, "protocol": "MQTT",
                "supports_telemetry": 1, "data_points": [{"code": "door_state"}], "event_types": []},
            "rules": [{"rule_id": "NW-LOCAL-RULE-S6", "rule_type": "STATE_CHANGE",
                "scope_type": "Device", "scope_ref": "NW-LOCAL-DR001",
                "event_type": "DOOR_OPENED", "open_limit_s": 300, "version": 3}]}
        with patch("integrations.dev1_config.apply_profile", return_value={}), \
             patch("integrations.dev1_config.apply_device", return_value={}):
            with self.assertRaisesRegex(ValueError, "requires business_start"):
                reconcile_device_config(ReverseCursor(), base)
        base["profile"]["supports_telemetry"] = 0
        with self.assertRaisesRegex(ValueError, "supports_telemetry conflicts"):
            reconcile_device_config(ReverseCursor(), base)


if __name__ == "__main__":
    unittest.main()
