"""Offline contract tests for Dev 1 ↔ Dev 2 integration.

Validates S1/S6 fixtures, contract validators, envelope shapes, identity mapping,
door state conversions, and fail-closed security rules without external calls.
"""
import copy
import json
import unittest
from pathlib import Path

from tests.e2e.dev1_dev2.contract import (
    validate_alarm_ack,
    validate_alarm_payload,
    validate_auth_headers,
    validate_command_ack_response,
    validate_command_claim_response,
    validate_config,
    validate_dev1_to_dev2_sync,
    validate_frappe_envelope,
    validate_if06_callback,
    validate_status_payload,
)

HERE = Path(__file__).resolve().parent


class OfflineContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s1_fixture = json.loads((HERE / "fixtures" / "s1.json").read_text(encoding="utf-8"))
        cls.s6_fixture = json.loads((HERE / "fixtures" / "s6.json").read_text(encoding="utf-8"))

    # -------------------------------------------------------------------------
    # Fixture Validity
    # -------------------------------------------------------------------------

    def test_s1_fixture_matches_contract(self):
        device_id = self.s1_fixture["device_id"]
        self.assertEqual(device_id, "NW-IT-TH001")
        validate_config(self.s1_fixture["config"], device_id)
        validate_status_payload(self.s1_fixture["status"], device_id)
        validate_alarm_payload(self.s1_fixture["alarm_occurrence"], device_id)
        validate_alarm_payload(self.s1_fixture["alarm_cleared"], device_id)

    def test_s6_fixture_matches_contract(self):
        device_id = self.s6_fixture["device_id"]
        self.assertEqual(device_id, "NW-IT-DR001")
        validate_config(self.s6_fixture["config"], device_id)
        validate_status_payload(self.s6_fixture["status"], device_id)
        validate_alarm_payload(self.s6_fixture["alarm_occurrence"], device_id)
        validate_alarm_payload(self.s6_fixture["alarm_cleared"], device_id)

    # -------------------------------------------------------------------------
    # Authentication & Envelope
    # -------------------------------------------------------------------------

    def test_auth_headers_require_token_key_and_secret(self):
        headers = {
            "Authorization": "token TEST_KEY:TEST_SECRET",
            "X-Frappe-Site-Name": "nextgrp.localhost",
            "Accept": "application/json",
        }
        self.assertEqual(validate_auth_headers(headers, "nextgrp.localhost"), headers)

        # Rejects single token without secret (Dev 1 contract requires KEY:SECRET)
        with self.assertRaisesRegex(ValueError, "API_KEY:API_SECRET"):
            validate_auth_headers({"Authorization": "token TEST_KEY_ONLY", "Accept": "application/json"})

        # Rejects missing Bearer or token prefix
        with self.assertRaisesRegex(ValueError, "must begin with 'token '"):
            validate_auth_headers({"Authorization": "Bearer TEST_KEY:TEST_SECRET", "Accept": "application/json"})

        # Rejects site mismatch
        with self.assertRaisesRegex(ValueError, "X-Frappe-Site-Name mismatch"):
            validate_auth_headers(headers, site_name="wrong.site")

    def test_frappe_envelope_validation(self):
        self.assertEqual(validate_frappe_envelope({"message": {}}), {"message": {}})
        with self.assertRaisesRegex(ValueError, "missing 'message' envelope"):
            validate_frappe_envelope({"data": {}})
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            validate_frappe_envelope("not-a-dict")

    # -------------------------------------------------------------------------
    # Alarm ACK Validation (Occurrence & Duplicate)
    # -------------------------------------------------------------------------

    def test_alarm_ack_first_occurrence(self):
        ack = {
            "alarm_key": "NW-IT-RULE-S1:NW-IT-TH001:1",
            "status": "New",
            "occurrence_count": 1,
            "changed": True,
            "duplicate": False,
        }
        self.assertEqual(validate_alarm_ack(ack, expected_alarm_key="NW-IT-RULE-S1:NW-IT-TH001:1", is_duplicate=False), ack)

        # Mismatches fail
        with self.assertRaisesRegex(ValueError, "duplicate mismatch"):
            validate_alarm_ack(ack, is_duplicate=True)

    def test_alarm_ack_duplicate_retry(self):
        ack = {
            "alarm_key": "NW-IT-RULE-S1:NW-IT-TH001:1",
            "occurrence_count": 1,
            "changed": False,
            "duplicate": True,
        }
        self.assertEqual(validate_alarm_ack(ack, expected_alarm_key="NW-IT-RULE-S1:NW-IT-TH001:1", is_duplicate=True), ack)

        # Duplicate with changed=True is rejected as invalid ACK
        invalid_dup = copy.deepcopy(ack)
        invalid_dup["changed"] = True
        with self.assertRaisesRegex(ValueError, "duplicate alarm ACK must have changed=False"):
            validate_alarm_ack(invalid_dup, is_duplicate=True)

    # -------------------------------------------------------------------------
    # Status Payload Validation
    # -------------------------------------------------------------------------

    def test_status_payload_validation(self):
        valid = {"device_id": "NW-IT-TH001", "org_code": "NW-IT-ORG", "event_id": "e-1", "state": "online"}
        self.assertEqual(validate_status_payload(valid, "NW-IT-TH001"), valid)

        valid_offline = copy.deepcopy(valid)
        valid_offline["state"] = "offline"
        self.assertEqual(validate_status_payload(valid_offline, "NW-IT-TH001"), valid_offline)

        with self.assertRaisesRegex(ValueError, "status state must be online or offline"):
            invalid = copy.deepcopy(valid)
            invalid["state"] = "unknown"
            validate_status_payload(invalid, "NW-IT-TH001")

        with self.assertRaisesRegex(ValueError, "device identity mismatch"):
            validate_status_payload(valid, "WRONG-DEVICE")

    # -------------------------------------------------------------------------
    # Alarm Payload Validation & Failure Cases
    # -------------------------------------------------------------------------

    def test_alarm_payload_validation_checks(self):
        base = copy.deepcopy(self.s1_fixture["alarm_occurrence"])

        # Mismatch alarm_key and episode_key
        bad_key = copy.deepcopy(base)
        bad_key["episode_key"] = "different-key"
        with self.assertRaisesRegex(ValueError, "alarm_key and episode_key must match"):
            validate_alarm_payload(bad_key, "NW-IT-TH001")

        # Invalid occurrence_seq
        bad_seq = copy.deepcopy(base)
        bad_seq["occurrence_seq"] = 0
        with self.assertRaisesRegex(ValueError, "occurrence_seq must be a positive integer"):
            validate_alarm_payload(bad_seq, "NW-IT-TH001")

        # Missing event_id
        bad_evt = copy.deepcopy(base)
        bad_evt["event_id"] = ""
        with self.assertRaisesRegex(ValueError, "event_id and org_code are required"):
            validate_alarm_payload(bad_evt, "NW-IT-TH001")

    # -------------------------------------------------------------------------
    # Configuration Failure Cases
    # -------------------------------------------------------------------------

    def test_config_validation_catches_contradictions(self):
        # supports_telemetry conflict
        bad_conf = copy.deepcopy(self.s1_fixture["config"])
        bad_conf["profile"]["supports_telemetry"] = False
        with self.assertRaisesRegex(ValueError, "supports_telemetry conflicts with data_points"):
            validate_config(bad_conf, "NW-IT-TH001")

        # Unknown data point referenced in rule
        bad_rule_metric = copy.deepcopy(self.s1_fixture["config"])
        bad_rule_metric["rules"][0]["data_point"] = "non_existent_metric"
        with self.assertRaisesRegex(ValueError, "rule references an unknown data point"):
            validate_config(bad_rule_metric, "NW-IT-TH001")

        # Scope ref mismatch
        bad_scope = copy.deepcopy(self.s1_fixture["config"])
        bad_scope["rules"][0]["scope_ref"] = "OTHER_DEVICE"
        with self.assertRaisesRegex(ValueError, "rule scope does not match device"):
            validate_config(bad_scope, "NW-IT-TH001")

    # -------------------------------------------------------------------------
    # Command Claim & ACK Responses
    # -------------------------------------------------------------------------

    def test_command_claim_response_validation(self):
        empty = {"command": None}
        self.assertEqual(validate_command_claim_response(empty), empty)

        cmd = {
            "command": {
                "command_id": "CMD-001",
                "device_id": "NW-IT-TH001",
                "command_code": "PING",
                "delivery_token": "token-xyz",
            }
        }
        self.assertEqual(validate_command_claim_response(cmd, "NW-IT-TH001"), cmd)

        with self.assertRaisesRegex(ValueError, "device_id mismatch"):
            validate_command_claim_response(cmd, "OTHER-DEVICE")

    def test_command_ack_response_validation(self):
        ack = {
            "command_id": "CMD-001",
            "status": "Succeeded",
            "duplicate": False,
        }
        self.assertEqual(validate_command_ack_response(ack, "CMD-001"), ack)

        with self.assertRaisesRegex(ValueError, "ack status must be Succeeded or Failed"):
            validate_command_ack_response({"command_id": "CMD-001", "status": "Pending", "duplicate": False})


if __name__ == "__main__":
    unittest.main()

