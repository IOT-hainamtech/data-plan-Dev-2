"""Unit tests for Dev 1 -> Dev 2 identity mapping engine and snapshot reconciliation.

Covers:
1. Unconfirmed mapping rejected (LookupError).
2. Confirmed mapping succeeds.
3. Duplicate external ID conflict rejected.
4. Duplicate internal ID conflict rejected.
5. Mapping organization mismatch rejected.
6. New rule mapping and alarm translation without guessing event types.
7. Business rule mismatch (kind/metric/event_type) rejected with 409.
8. Rule version increase and immutable version history recording.
9. Rule version downgrade rejected with 409.
10. Snapshot worker dynamic device discovery.
11. Snapshot worker fail-closed on mapping error while status reconciliation continues.
"""
import copy
import hashlib
import json
import unittest
from datetime import datetime, timezone
from fastapi import HTTPException

from integrations.mapping import (
    validate_mapping_record,
    apply_mappings,
    discover_confirmed_devices,
    load_mappings_manifest,
)
from integrations.dev1_config import (
    resolve_confirmed_mapping,
    resolve_internal_mapping,
    map_alarm_payload,
)
from registry.sync import apply_rule, payload_hash
from snapshot_worker import run_reconciliation_cycle


class MockDBConnection:
    def __init__(self):
        self.organizations = {
            "hainam": {"enabled": True, "display_name": "Hai Nam", "version": 1}
        }
        self.sites = {
            ("hainam", "hq"): {"org_code": "hainam", "site_code": "hq", "enabled": True, "display_name": "HQ", "version": 1}
        }
        self.zones = {
            ("hainam", "hq", "LAB"): {"org_code": "hainam", "site_code": "hq", "zone_code": "LAB", "enabled": True, "version": 1}
        }
        self.device_profiles = {
            "TEMP_HUM_SENSOR": {"enabled": True, "metadata_json": {"signal_model": "TELEMETRY"}, "version": 1},
            "DOOR_SENSOR": {"enabled": True, "metadata_json": {"signal_model": "EVENT"}, "version": 1},
        }
        self.devices = {
            "TH-001": {"org_code": "hainam", "site_code": "hq", "zone_code": "LAB", "profile_code": "TEMP_HUM_SENSOR", "enabled": True, "version": 1},
            "DR-001": {"org_code": "hainam", "site_code": "hq", "zone_code": "LAB", "profile_code": "DOOR_SENSOR", "enabled": True, "version": 1},
        }
        # rule_code -> dict
        self.rules = {
            "S1_TEMP_HIGH": {
                "rule_code": "S1_TEMP_HIGH", "device_id": "TH-001", "rule_kind": "THRESHOLD",
                "metric": "temperature", "event_type": None, "hold_seconds": 0,
                "threshold_value": 30.0, "enabled": True, "config_version": 4,
                "payload_hash": "hash_v4", "config_json": {"rule_kind": "THRESHOLD", "metric": "temperature"}
            },
            "S6_DOOR_OPEN": {
                "rule_code": "S6_DOOR_OPEN", "device_id": "DR-001", "rule_kind": "STATE_CHANGE",
                "metric": None, "event_type": "DOOR_OPENED", "hold_seconds": 300,
                "threshold_value": None, "enabled": True, "config_version": 3,
                "payload_hash": "hash_v3", "config_json": {"rule_kind": "STATE_CHANGE", "event_type": "DOOR_OPENED"}
            }
        }
        # (system_code, entity_type, internal_id) -> dict
        self.mappings = {}
        # list of dicts: system_code, entity_type, internal_id, external_id, config_version, identity_hash
        self.mapping_versions = []
        self.alarm_outbox = []
        self.alarm_episode_mappings = {}

    def cursor(self):
        return MockCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


class MockCursor:
    def __init__(self, db: MockDBConnection):
        self.db = db
        self._last_row = None
        self._last_rows = []

    def execute(self, sql, args=None):
        args = args or ()
        sql_clean = " ".join(sql.strip().split())
        sql_upper = sql_clean.upper()

        self._last_row = None
        self._last_rows = []

        # 1. ORGANIZATIONS
        if "FROM ORGANIZATIONS" in sql_upper and "WHERE ORG_CODE=" in sql_upper:
            org = self.db.organizations.get(args[0])
            if org:
                self._last_row = (org["enabled"],)
            return

        # 2. SITES
        if "FROM SITES" in sql_upper and "WHERE SITE_CODE=" in sql_upper:
            # Look up site by site_code
            for (o, s), site in self.db.sites.items():
                if s == args[0]:
                    self._last_row = (site["org_code"], site["enabled"])
                    break
            return

        # 3. ZONES
        if "FROM ZONES" in sql_upper and "WHERE ZONE_CODE=" in sql_upper:
            for (o, s, z), zone in self.db.zones.items():
                if z == args[0]:
                    self._last_row = (zone["org_code"], zone["site_code"], zone["enabled"])
                    break
            return

        # 4. DEVICE_PROFILES
        if "FROM DEVICE_PROFILES" in sql_upper and "WHERE PROFILE_CODE=" in sql_upper:
            prof = self.db.device_profiles.get(args[0])
            if prof:
                self._last_row = (prof["enabled"], prof["metadata_json"])
            return

        # 5. DEVICES
        if "FROM DEVICES WHERE DEVICE_ID=" in sql_upper and "AND ORG_CODE=" in sql_upper:
            dev = self.db.devices.get(args[0])
            if dev and dev["org_code"] == args[1]:
                self._last_row = (1,)
            return

        if "FROM DEVICES" in sql_upper and "WHERE DEVICE_ID=" in sql_upper:
            dev = self.db.devices.get(args[0])
            if dev:
                self._last_row = (dev["org_code"], dev["site_code"], dev["zone_code"], dev["profile_code"], dev["enabled"])
            return

        # 6. RULE_DEFINITIONS
        if "SELECT CONFIG_VERSION,PAYLOAD_HASH FROM RULE_DEFINITIONS" in sql_upper:
            r = self.db.rules.get(args[0])
            if r:
                self._last_row = (r["config_version"], r["payload_hash"])
            return

        if "SELECT RULE_KIND, METRIC, EVENT_TYPE FROM RULE_DEFINITIONS" in sql_upper:
            r = self.db.rules.get(args[0])
            if r:
                self._last_row = (r["rule_kind"], r["metric"], r["event_type"])
            return

        if "SELECT DEVICE_ID, RULE_KIND, METRIC, EVENT_TYPE" in sql_upper:
            r = self.db.rules.get(args[0])
            if r:
                self._last_row = (r["device_id"], r["rule_kind"], r["metric"], r["event_type"],
                                  r["hold_seconds"], r["threshold_value"], r["enabled"], r["config_version"])
            return

        if "SELECT CONFIG_VERSION,CONFIG_JSON FROM RULE_DEFINITIONS" in sql_upper:
            r = self.db.rules.get(args[0])
            if r and r["enabled"]:
                self._last_row = (r["config_version"], r["config_json"])
            return

        if "INSERT INTO RULE_DEFINITIONS" in sql_upper:
            # INSERT ... ON CONFLICT(rule_code) DO UPDATE
            code = args[0]
            device = args[1]
            kind = args[2]
            metric = args[3]
            event_type = args[4]
            thresh = args[5]
            hyst = args[6]
            hold = args[7]
            clear_hold = args[8]
            quiet = args[9]
            sev = args[10]
            enab = args[11]
            b_start = args[12]
            b_end = args[13]
            tz = args[14]
            ver = args[20]
            digest = args[21]
            cfg = args[22]
            self.db.rules[code] = {
                "rule_code": code, "device_id": device, "rule_kind": kind,
                "metric": metric, "event_type": event_type, "threshold_value": thresh,
                "hysteresis_value": hyst, "hold_seconds": hold, "clear_hold_seconds": clear_hold,
                "quiet_seconds": quiet, "severity": sev, "enabled": enab,
                "business_start": b_start, "business_end": b_end, "schedule_timezone": tz,
                "config_version": ver, "payload_hash": digest, "config_json": cfg
            }
            return

        # 7. INTEGRATION_IDENTITY_MAPPINGS
        if "FROM INTEGRATION_IDENTITY_MAPPINGS" in sql_upper:
            sys_code = args[0]

            if "SELECT EXTERNAL_ID FROM INTEGRATION_IDENTITY_MAPPINGS" in sql_upper and "ENTITY_TYPE='DEVICE'" in sql_upper:
                rows = []
                for k, rec in sorted(self.db.mappings.items()):
                    if rec["system_code"] == sys_code and rec["entity_type"] == "DEVICE" and rec["confirmed"] and rec["enabled"]:
                        rows.append((rec["external_id"],))
                self._last_rows = sorted(rows, key=lambda x: x[0])
                return

            etype = args[1] if len(args) > 1 else "RULE"

            if "EXTERNAL_ID, CONFIRMED FROM" in sql_upper and "INTERNAL_ID=" in sql_upper:
                iid = args[2]
                rec = self.db.mappings.get((sys_code, etype, iid))
                if rec:
                    self._last_row = (rec["external_id"], rec["confirmed"])
                return

            if "INTERNAL_ID, CONFIRMED FROM" in sql_upper and "EXTERNAL_ID=" in sql_upper:
                xid = args[2]
                for k, rec in self.db.mappings.items():
                    if rec["system_code"] == sys_code and rec["entity_type"] == etype and rec["external_id"] == xid:
                        self._last_row = (rec["internal_id"], rec["confirmed"])
                        break
                return

            if "EXTERNAL_ID, CONFIG_VERSION, CONFIRMED, ENABLED, METADATA_JSON" in sql_upper:
                iid = args[2]
                rec = self.db.mappings.get((sys_code, etype, iid))
                if rec:
                    self._last_row = (rec["external_id"], rec["config_version"], rec["confirmed"], rec["enabled"], rec["metadata_json"])
                return

            if "EXTERNAL_ID,CONFIG_VERSION FROM INTEGRATION_IDENTITY_MAPPINGS" in sql_upper and "CONFIRMED=TRUE" in sql_upper:
                # resolve_confirmed_mapping passes (system_code, entity_type, internal_id) -> 3 args
                # _sync_confirmed_rule_mapping passes (system_code, rule_code) -> 2 args
                if len(args) == 2:
                    sys_code, etype, iid = args[0], "RULE", args[1]
                else:
                    sys_code, etype, iid = args[0], args[1], args[2]
                rec = self.db.mappings.get((sys_code, etype, iid))
                if rec and rec["confirmed"] and rec["enabled"]:
                    self._last_row = (rec["external_id"], rec["config_version"])
                return

            if "INTERNAL_ID,CONFIG_VERSION FROM INTEGRATION_IDENTITY_MAPPINGS" in sql_upper and "CONFIRMED=TRUE" in sql_upper:
                # resolve_internal_mapping
                xid = args[2]
                for k, rec in self.db.mappings.items():
                    if rec["system_code"] == sys_code and rec["entity_type"] == etype and rec["external_id"] == xid and rec["confirmed"] and rec["enabled"]:
                        self._last_row = (rec["internal_id"], rec["config_version"])
                        break
                return

        if "INSERT INTO INTEGRATION_IDENTITY_MAPPINGS" in sql_upper:
            # (sys_code, etype, iid, xid, ver, conf, enab, meta_json)
            sys_code, etype, iid, xid, ver, conf, enab, meta_json = args
            meta = json.loads(meta_json) if isinstance(meta_json, str) else meta_json
            existing = self.db.mappings.get((sys_code, etype, iid))
            if existing:
                existing["external_id"] = xid
                existing["config_version"] = max(existing["config_version"], ver)
                existing["confirmed"] = existing["confirmed"] or conf
                existing["enabled"] = enab
                existing["metadata_json"].update(meta)
            else:
                self.db.mappings[(sys_code, etype, iid)] = {
                    "system_code": sys_code, "entity_type": etype,
                    "internal_id": iid, "external_id": xid,
                    "config_version": ver, "confirmed": conf, "enabled": enab,
                    "metadata_json": meta
                }
            return

        if "UPDATE INTEGRATION_IDENTITY_MAPPINGS SET CONFIG_VERSION=" in sql_upper:
            ver, sys_code, iid = args[0], args[1], args[2]
            key = (sys_code, "RULE", iid)
            if key in self.db.mappings:
                self.db.mappings[key]["config_version"] = ver
            return

        if "INSERT INTO INTEGRATION_IDENTITY_MAPPING_VERSIONS" in sql_upper:
            if len(args) == 5:
                sys_code, iid, xid, ver, ident_hash = args
                etype = "RULE"
            else:
                sys_code, etype, iid, xid, ver, ident_hash = args
            self.db.mapping_versions.append({
                "system_code": sys_code, "entity_type": etype,
                "internal_id": iid, "external_id": xid,
                "config_version": ver, "identity_hash": ident_hash
            })
            return

        # 8. ALARM OUTBOX & EPISODES
        if "FROM ALARM_OUTBOX" in sql_upper:
            return
        if "FROM ALARM_EPISODE_MAPPINGS" in sql_upper:
            return

    def fetchone(self):
        return self._last_row

    def fetchall(self):
        return self._last_rows


class Dev1MappingTests(unittest.TestCase):
    def setUp(self):
        self.db = MockDBConnection()
        self.cur = self.db.cursor()

    # -------------------------------------------------------------------------
    # Test 1: Unconfirmed mapping rejected
    # -------------------------------------------------------------------------
    def test_01_unconfirmed_mapping_rejected(self):
        records = [{
            "system_code": "nextgrp",
            "entity_type": "DEVICE",
            "internal_id": "TH-001",
            "external_id": "NW-IT-TH001",
            "config_version": 1,
            "confirmed": False,
            "enabled": True,
            "metadata": {"org_code": "hainam"}
        }]
        res = apply_mappings(self.cur, records)
        self.assertEqual(res["created"], 1)

        # Unconfirmed mapping must raise LookupError in runtime resolution
        with self.assertRaises(LookupError) as ctx:
            resolve_confirmed_mapping(self.cur, "DEVICE", "TH-001")
        self.assertIn("no confirmed DEVICE mapping", str(ctx.exception))

        # Reverse lookup also rejects unconfirmed mapping
        with self.assertRaises(LookupError) as ctx:
            resolve_internal_mapping(self.cur, "DEVICE", "NW-IT-TH001")
        self.assertIn("no confirmed DEVICE mapping", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Test 2: Confirmed mapping succeeds
    # -------------------------------------------------------------------------
    def test_02_confirmed_mapping_succeeds(self):
        records = [{
            "system_code": "nextgrp",
            "entity_type": "DEVICE",
            "internal_id": "TH-001",
            "external_id": "NW-IT-TH001",
            "config_version": 2,
            "confirmed": True,
            "enabled": True,
            "metadata": {"org_code": "hainam"}
        }]
        res = apply_mappings(self.cur, records)
        self.assertEqual(res["created"], 1)

        xid, ver = resolve_confirmed_mapping(self.cur, "DEVICE", "TH-001")
        self.assertEqual(xid, "NW-IT-TH001")
        self.assertEqual(ver, 2)

        iid, ver2 = resolve_internal_mapping(self.cur, "DEVICE", "NW-IT-TH001")
        self.assertEqual(iid, "TH-001")
        self.assertEqual(ver2, 2)

    # -------------------------------------------------------------------------
    # Test 3: Duplicate external ID conflict rejected
    # -------------------------------------------------------------------------
    def test_03_duplicate_external_id_conflict_rejected(self):
        # Map TH-001 to NW-SHARED-ID
        apply_mappings(self.cur, [{
            "system_code": "nextgrp", "entity_type": "DEVICE",
            "internal_id": "TH-001", "external_id": "NW-SHARED-ID",
            "confirmed": True, "enabled": True
        }])

        # Attempt to map DR-001 to the same external_id NW-SHARED-ID
        with self.assertRaises(ValueError) as ctx:
            apply_mappings(self.cur, [{
                "system_code": "nextgrp", "entity_type": "DEVICE",
                "internal_id": "DR-001", "external_id": "NW-SHARED-ID",
                "confirmed": True, "enabled": True
            }])
        self.assertIn("Mapping conflict: external DEVICE 'NW-SHARED-ID' is already mapped to 'TH-001'", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Test 4: Duplicate internal ID conflict rejected
    # -------------------------------------------------------------------------
    def test_04_duplicate_internal_id_conflict_rejected(self):
        # Map TH-001 to NW-IT-TH001
        apply_mappings(self.cur, [{
            "system_code": "nextgrp", "entity_type": "DEVICE",
            "internal_id": "TH-001", "external_id": "NW-IT-TH001",
            "confirmed": True, "enabled": True
        }])

        # Attempt to map TH-001 to a different external ID NW-NEW-TH001
        with self.assertRaises(ValueError) as ctx:
            apply_mappings(self.cur, [{
                "system_code": "nextgrp", "entity_type": "DEVICE",
                "internal_id": "TH-001", "external_id": "NW-NEW-TH001",
                "confirmed": True, "enabled": True
            }])
        self.assertIn("Mapping conflict: internal DEVICE 'TH-001' is already mapped to 'NW-IT-TH001'", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Test 5: Mapping organization mismatch rejected
    # -------------------------------------------------------------------------
    def test_05_mapping_organization_mismatch_rejected(self):
        # Site hq belongs to org hainam; metadata claiming it belongs to foreign_org must fail
        with self.assertRaises(ValueError) as ctx:
            apply_mappings(self.cur, [{
                "system_code": "nextgrp", "entity_type": "SITE",
                "internal_id": "hq", "external_id": "NW-SITE-HQ",
                "confirmed": True, "metadata": {"org_code": "foreign_org"}
            }])
        self.assertIn("belongs to org 'hainam', expected 'foreign_org'", str(ctx.exception))

        # Device TH-001 belongs to org hainam
        with self.assertRaises(ValueError) as ctx:
            apply_mappings(self.cur, [{
                "system_code": "nextgrp", "entity_type": "DEVICE",
                "internal_id": "TH-001", "external_id": "NW-DEV-TH1",
                "confirmed": True, "metadata": {"org_code": "foreign_org"}
            }])
        self.assertIn("belongs to org 'hainam', expected 'foreign_org'", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Test 6: New rule mapping and alarm translation without guessing event types
    # -------------------------------------------------------------------------
    def test_06_new_rule_mapping_and_alarm_translation(self):
        # Set up confirmed org and device mappings
        apply_mappings(self.cur, [
            {"entity_type": "ORGANIZATION", "internal_id": "hainam", "external_id": "NW-LOCAL-ORG-001", "confirmed": True},
            {"entity_type": "DEVICE", "internal_id": "DR-001", "external_id": "NW-IT-DR001", "confirmed": True},
        ])

        # New AI Rule ML-LIVE-S6-f2dc024bb2 (STATE_CHANGE, AI_ANOMALY_DETECTED)
        self.db.rules["ML-LIVE-S6-f2dc024bb2"] = {
            "rule_code": "ML-LIVE-S6-f2dc024bb2", "device_id": "DR-001",
            "rule_kind": "STATE_CHANGE", "metric": None,
            "event_type": "AI_ANOMALY_DETECTED", "hold_seconds": 0, "threshold_value": None,
            "enabled": True, "config_version": 1, "payload_hash": "hash_ai",
            "config_json": {"rule_kind": "STATE_CHANGE", "event_type": "AI_ANOMALY_DETECTED"}
        }

        # Apply confirmed mapping for the new rule
        apply_mappings(self.cur, [{
            "entity_type": "RULE",
            "internal_id": "ML-LIVE-S6-f2dc024bb2",
            "external_id": "ML-LIVE-S6-f2dc024bb2",
            "config_version": 1,
            "confirmed": True,
            "enabled": True,
            "metadata": {
                "rule_kind": "STATE_CHANGE",
                "event_type": "AI_ANOMALY_DETECTED",
                "hold_seconds": 0
            }
        }])

        # Map an alarm occurrence for this new rule
        occurrence_payload = {
            "event_id": "evt-ai-1",
            "org_code": "hainam",
            "device_id": "DR-001",
            "rule_id": "ML-LIVE-S6-f2dc024bb2",
            "rule_version": 1,
            "kind": "occurrence",
            "episode_start": "2026-11-10T12:00:00Z",
            "event": {"event_type": "AI_ANOMALY_DETECTED"}
        }
        mapped = map_alarm_payload(self.cur, occurrence_payload)
        self.assertEqual(mapped["org_code"], "NW-LOCAL-ORG-001")
        self.assertEqual(mapped["device_id"], "NW-IT-DR001")
        self.assertEqual(mapped["rule_id"], "ML-LIVE-S6-f2dc024bb2")
        self.assertEqual(mapped["rule_version"], 1)
        self.assertEqual(mapped["alarm_key"], "ML-LIVE-S6-f2dc024bb2:NW-IT-DR001:1794312000")
        self.assertEqual(mapped["event"]["event_type"], "AI_ANOMALY_DETECTED")

        # Also verify new rule NW-IT-RULE-S12 (duration 300s) does NOT overwrite S1_TEMP_HIGH (duration 0s)
        self.assertEqual(self.db.rules["S1_TEMP_HIGH"]["hold_seconds"], 0)
        apply_mappings(self.cur, [{
            "entity_type": "RULE",
            "internal_id": "NW-IT-RULE-S12",
            "external_id": "NW-IT-RULE-S12",
            "config_version": 1,
            "confirmed": True,
            "enabled": True,
            "metadata": {
                "rule_kind": "THRESHOLD",
                "metric": "temperature",
                "hold_seconds": 300,
                "threshold": 30.0
            }
        }])
        self.assertEqual(self.db.rules["S1_TEMP_HIGH"]["hold_seconds"], 0)

    # -------------------------------------------------------------------------
    # Test 7: Rule mismatch (different rule_kind or metric) rejected with 409
    # -------------------------------------------------------------------------
    def test_07_rule_mismatch_rejected_with_409(self):
        # Existing S1_TEMP_HIGH is THRESHOLD on metric temperature
        # Attempt to apply a rule with same rule_id but different rule_kind STATE_CHANGE
        conflicting_rule = {
            "rule_id": "S1_TEMP_HIGH",
            "rule_kind": "STATE_CHANGE",
            "event_type": "TEMP_SPIKE",
            "scope": {"type": "DEVICE", "device_id": "TH-001"},
            "org_code": "hainam",
            "severity": "High",
            "version": 5,
            "enabled": True
        }
        with self.assertRaises(HTTPException) as ctx:
            apply_rule(self.cur, conflicting_rule)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "BUSINESS_RULE_MISMATCH")
        self.assertIn("rule_kind mismatch", ctx.exception.detail["reason"])

        # Attempt to apply with same rule_kind THRESHOLD but different metric 'humidity'
        metric_mismatch_rule = {
            "rule_id": "S1_TEMP_HIGH",
            "rule_kind": "THRESHOLD",
            "metric": "humidity",
            "threshold": 80.0,
            "scope": {"type": "DEVICE", "device_id": "TH-001"},
            "org_code": "hainam",
            "severity": "High",
            "version": 5,
            "enabled": True
        }
        with self.assertRaises(HTTPException) as ctx:
            apply_rule(self.cur, metric_mismatch_rule)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "BUSINESS_RULE_MISMATCH")
        self.assertIn("metric mismatch", ctx.exception.detail["reason"])

    # -------------------------------------------------------------------------
    # Test 8: Rule version increase and version history recording
    # -------------------------------------------------------------------------
    def test_08_rule_version_increase_and_history(self):
        # Set up confirmed mapping for S1_TEMP_HIGH at version 4
        apply_mappings(self.cur, [{
            "entity_type": "RULE", "internal_id": "S1_TEMP_HIGH",
            "external_id": "NW-IT-RULE-S1", "config_version": 4,
            "confirmed": True, "enabled": True
        }])

        # Apply updated rule with version 5
        updated_rule = {
            "rule_id": "S1_TEMP_HIGH",
            "rule_kind": "THRESHOLD",
            "metric": "temperature",
            "threshold": 32.0,
            "scope": {"type": "DEVICE", "device_id": "TH-001"},
            "org_code": "hainam",
            "severity": "High",
            "version": 5,
            "enabled": True
        }
        res = apply_rule(self.cur, updated_rule, sync_mapping=True, mapping_external_id="NW-IT-RULE-S1")
        self.assertEqual(res["status"], "applied")
        self.assertEqual(res["applied_version"], 5)

        # Check that version history has been recorded
        versions = [v for v in self.db.mapping_versions if v["internal_id"] == "S1_TEMP_HIGH"]
        self.assertTrue(any(v["config_version"] == 5 for v in versions))

    # -------------------------------------------------------------------------
    # Test 9: Rule version downgrade rejected
    # -------------------------------------------------------------------------
    def test_09_rule_version_downgrade_rejected(self):
        # Current version is 4. Attempt to apply version 3.
        stale_rule = {
            "rule_id": "S1_TEMP_HIGH",
            "rule_kind": "THRESHOLD",
            "metric": "temperature",
            "threshold": 30.0,
            "scope": {"type": "DEVICE", "device_id": "TH-001"},
            "org_code": "hainam",
            "severity": "High",
            "version": 3,
            "enabled": True
        }
        with self.assertRaises(HTTPException) as ctx:
            apply_rule(self.cur, stale_rule)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "STALE_VERSION")

    # -------------------------------------------------------------------------
    # Test 10: Snapshot worker dynamic device discovery
    # -------------------------------------------------------------------------
    def test_10_snapshot_worker_dynamic_device_discovery(self):
        # Register UNCONF-01 in devices table
        self.db.devices["UNCONF-01"] = {
            "org_code": "hainam", "site_code": "hq", "zone_code": "LAB",
            "profile_code": "TEMP_HUM_SENSOR", "enabled": True, "version": 1
        }
        # Add confirmed and unconfirmed devices
        apply_mappings(self.cur, [
            {"entity_type": "DEVICE", "internal_id": "TH-001", "external_id": "NW-IT-TH001", "confirmed": True, "enabled": True},
            {"entity_type": "DEVICE", "internal_id": "DR-001", "external_id": "NW-IT-DR001", "confirmed": True, "enabled": True},
            {"entity_type": "DEVICE", "internal_id": "UNCONF-01", "external_id": "NW-IT-UNCONF", "confirmed": False, "enabled": True},
        ])
        discovered = discover_confirmed_devices(self.cur)
        self.assertEqual(discovered, ["NW-IT-DR001", "NW-IT-TH001"])

    # -------------------------------------------------------------------------
    # Test 11: Snapshot worker fail-closed on mapping error while status reconciliation continues
    # -------------------------------------------------------------------------
    def test_11_snapshot_worker_fail_closed_status_continues(self):
        calls = []

        def failing_config():
            raise LookupError("no confirmed mapping for device")

        def successful_status():
            calls.append("status_reconciled")

        # run_reconciliation_cycle must execute status reconciliation even if config reconciliation fails
        run_reconciliation_cycle(failing_config, successful_status)
        self.assertEqual(calls, ["status_reconciled"])


if __name__ == "__main__":
    unittest.main()
