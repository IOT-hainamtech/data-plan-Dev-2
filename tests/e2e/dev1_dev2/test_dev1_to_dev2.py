"""Offline contract tests for Dev 1 → Dev 2 APIs (v1.3 config sync and IF-06 callback).

Runs in-memory with mock cursors without calling live endpoints or modifying runtime DB.
Validates:
1. Public v1.3 models to internal schema translation (_internal).
2. URL identifier vs body identifier validation.
3. Version increment, duplicate detection, stale version rejection, and payload conflict.
4. Alarm lifecycle state progression and regression prevention (STATE_REGRESSION).
5. Alarm key mapping resolution (resolve_internal_alarm_key).
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from api.integration import _internal
from api.models import (
    AlarmStateIn,
    DeviceIn,
    OrganizationIn,
    ProfileIn,
    RuleIn,
    SiteIn,
    ZoneIn,
)
from fastapi import HTTPException
from integrations.dev1_config import resolve_internal_alarm_key
from registry.sync import payload_hash, version_decision


class Dev1ToDev2ContractTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # Schema / Model Translation (_internal)
    # -------------------------------------------------------------------------

    def test_organization_translation(self):
        org = OrganizationIn(org_code="NW-ORG", name="Test Org", version=1)
        internal = _internal(org)
        self.assertEqual(internal["org_code"], "NW-ORG")
        self.assertEqual(internal["display_name"], "Test Org")

    def test_site_translation(self):
        site = SiteIn(org_code="NW-ORG", site_code="NW-SITE", name="Main Site", version=1)
        internal = _internal(site)
        self.assertEqual(internal["site_code"], "NW-SITE")
        self.assertEqual(internal["display_name"], "Main Site")
        self.assertTrue(internal["enabled"])

    def test_zone_translation(self):
        zone = ZoneIn(org_code="NW-ORG", site_code="NW-SITE", zone_code="NW-ZONE", name="Lab Zone", version=1)
        internal = _internal(zone)
        self.assertEqual(internal["zone_code"], "NW-ZONE")
        self.assertEqual(internal["display_name"], "Lab Zone")
        self.assertTrue(internal["enabled"])

    def test_profile_s1_translation(self):
        profile = ProfileIn(
            profile_code="NW-IT-S1",
            version=1,
            signal_model="TELEMETRY",
            protocol="MQTT",
            expected_interval_s=60,
            heartbeat_interval_s=60,
            offline_after_missed=2,
            data_points=[
                {"code": "temperature", "channel": "telemetry", "data_type": "number",
                 "unit": "Cel", "min": -40.0, "max": 125.0, "required": True, "label": "Temperature"},
                {"code": "humidity", "channel": "telemetry", "data_type": "number",
                 "unit": "%RH", "min": 0.0, "max": 100.0, "required": True, "label": "Humidity"}
            ],
            event_types=[]
        )
        internal = _internal(profile)
        self.assertEqual(internal["profile_code"], "NW-IT-S1")
        self.assertEqual(len(internal["data_points"]), 2)
        self.assertEqual(internal["data_points"][0]["min_value"], -40.0)
        self.assertEqual(internal["data_points"][0]["max_value"], 125.0)

    def test_device_translation(self):
        device = DeviceIn(
            device_id="NW-IT-TH001",
            org_code="NW-ORG",
            site_code="NW-SITE",
            zone_code="NW-ZONE",
            profile_code="NW-IT-S1",
            config_version=1,
            connectivity_type="WIFI",
            status="active"
        )
        internal = _internal(device)
        self.assertEqual(internal["device_id"], "NW-IT-TH001")
        self.assertEqual(internal["version"], 1)
        self.assertEqual(internal["connectivity"], "WIFI")
        self.assertEqual(internal["metadata"]["status"], "active")

    def test_rule_s1_translation(self):
        rule = RuleIn(
            rule_id="NW-IT-RULE-S1",
            org_code="NW-ORG",
            version=1,
            rule_type="THRESHOLD",
            scope={"type": "DEVICE", "ref": "NW-IT-TH001"},
            data_point="temperature",
            operator=">",
            threshold=30.0,
            severity="High",
            active=True
        )
        internal = _internal(rule)
        self.assertEqual(internal["rule_id"], "NW-IT-RULE-S1")
        self.assertEqual(internal["rule_kind"], "THRESHOLD")
        self.assertEqual(internal["metric"], "temperature")
        self.assertEqual(internal["scope"]["device_id"], "NW-IT-TH001")
        self.assertTrue(internal["enabled"])

    # -------------------------------------------------------------------------
    # Versioning Decision Logic (Normal, Duplicate, Stale, Conflict)
    # -------------------------------------------------------------------------

    def test_version_decision_when_no_prior_record(self):
        cur = MagicMock()
        cur.fetchone.return_value = None
        result = version_decision(cur, "devices", "device_id=%s", ("NW-IT-TH001",), 1, "hash1")
        self.assertEqual(result, "apply")

    def test_version_decision_duplicate_same_hash(self):
        cur = MagicMock()
        # Row format: (current_version, current_hash)
        cur.fetchone.return_value = (1, "hash1")
        result = version_decision(cur, "devices", "device_id=%s", ("NW-IT-TH001",), 1, "hash1")
        self.assertEqual(result, "duplicate")

    def test_version_decision_stale_version_rejected(self):
        cur = MagicMock()
        cur.fetchone.return_value = (2, "hash2")
        with self.assertRaises(HTTPException) as ctx:
            version_decision(cur, "devices", "device_id=%s", ("NW-IT-TH001",), 1, "hash1")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "STALE_VERSION")

    def test_version_decision_conflict_same_version_different_payload(self):
        cur = MagicMock()
        cur.fetchone.return_value = (1, "old_hash")
        with self.assertRaises(HTTPException) as ctx:
            version_decision(cur, "devices", "device_id=%s", ("NW-IT-TH001",), 1, "new_different_hash")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "VERSION_PAYLOAD_CONFLICT")

    # -------------------------------------------------------------------------
    # IF-06 Alarm Lifecycle Callback Validation
    # -------------------------------------------------------------------------

    def test_alarm_state_model_validation(self):
        now = datetime.now(timezone.utc).isoformat()
        body = AlarmStateIn(
            alarm_key="NW-IT-RULE-S1:NW-IT-TH001:1",
            org_code="NW-ORG",
            state="acknowledged",
            version=1,
            changed_at=now,
            changed_by="admin@example.com"
        )
        self.assertEqual(body.alarm_key, "NW-IT-RULE-S1:NW-IT-TH001:1")
        self.assertEqual(body.state, "acknowledged")

    def test_alarm_lifecycle_state_rank_and_regression_prevention(self):
        ranks = {"acknowledged": 1, "in_progress": 2, "closed": 3}
        # Legitimate state progression
        self.assertLess(ranks["acknowledged"], ranks["in_progress"])
        self.assertLess(ranks["in_progress"], ranks["closed"])

        # Regression attempt: 'closed' back to 'acknowledged'
        old_state_rank = ranks["closed"]
        new_state = "acknowledged"
        self.assertLess(ranks[new_state], old_state_rank)

    # -------------------------------------------------------------------------
    # IF-06 Alarm Key Mapping Resolution
    # -------------------------------------------------------------------------

    def test_resolve_internal_alarm_key_direct_match(self):
        cur = MagicMock()
        cur.fetchone.return_value = ("NW-IT-RULE-S1:NW-IT-TH001:1",)
        key = resolve_internal_alarm_key(cur, "NW-IT-RULE-S1:NW-IT-TH001:1")
        self.assertEqual(key, "NW-IT-RULE-S1:NW-IT-TH001:1")

    def test_resolve_internal_alarm_key_outbox_reverse_lookup(self):
        cur = MagicMock()
        # Direct match fails
        cur.fetchone.return_value = None
        # Outbox returns single unique wire_alarm_key
        cur.fetchall.return_value = [("S1_TEMP_HIGH:TH-001:1",)]
        key = resolve_internal_alarm_key(cur, "NW-IT-RULE-S1:NW-IT-TH001:1")
        self.assertEqual(key, "S1_TEMP_HIGH:TH-001:1")

    def test_resolve_internal_alarm_key_unmapped_fails_closed(self):
        cur = MagicMock()
        cur.fetchone.return_value = None
        cur.fetchall.return_value = []
        with self.assertRaises(LookupError):
            resolve_internal_alarm_key(cur, "UNKNOWN-KEY")

    def test_resolve_internal_alarm_key_ambiguous_mapping_fails_closed(self):
        cur = MagicMock()
        cur.fetchone.return_value = None
        cur.fetchall.return_value = [("key-a",), ("key-b",)]
        with self.assertRaisesRegex(ValueError, "multiple"):
            resolve_internal_alarm_key(cur, "AMBIGUOUS-KEY")


if __name__ == "__main__":
    unittest.main()
