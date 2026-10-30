"""Strict Phase-1 fixture/response validation; no database side effects."""
from datetime import time as wall_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_auth_headers(headers, site_name=None):
    """Validate HTTP headers sent to Dev 1 Frappe instance.

    Dev 1 contract:
    - Authorization: token API_KEY:API_SECRET
    - X-Frappe-Site-Name: nextgrp.localhost
    - Accept: application/json
    """
    if not isinstance(headers, dict):
        raise ValueError("headers must be a dictionary")
    # Normalized lookup (case-insensitive)
    lower_headers = {k.lower(): v for k, v in headers.items()}
    auth = lower_headers.get("authorization")
    if not auth or not auth.startswith("token "):
        raise ValueError("Authorization header must begin with 'token '")
    cred = auth[6:].strip()
    if ":" not in cred:
        raise ValueError("Authorization token must contain 'API_KEY:API_SECRET'")
    parts = cred.split(":", 1)
    if not parts[0].strip() or not parts[1].strip():
        raise ValueError("Authorization token must have non-empty API_KEY and API_SECRET")
    if site_name:
        site = lower_headers.get("x-frappe-site-name")
        if site != site_name:
            raise ValueError(f"X-Frappe-Site-Name mismatch: expected {site_name}, got {site}")
    accept = lower_headers.get("accept")
    if accept != "application/json":
        raise ValueError("Accept header must be 'application/json'")
    return headers


def validate_frappe_envelope(response_body):
    """Validate that Frappe response has the required 'message' envelope."""
    if not isinstance(response_body, dict):
        raise ValueError("Frappe response must be a JSON object")
    if "message" not in response_body:
        raise ValueError("Frappe response is missing 'message' envelope")
    return response_body


def validate_config(config, expected_device_id):
    if not isinstance(config, dict):
        raise ValueError("config message must be an object")
    device, profile, rules = config.get("device"), config.get("profile"), config.get("rules")
    if not isinstance(device, dict) or device.get("device_id") != expected_device_id:
        raise ValueError("config device identity mismatch")
    _version(device.get("config_version"), "config_version")
    if device.get("zone") is not None and not isinstance(device.get("zone"), str):
        raise ValueError("zone must be a string or null")
    if not isinstance(profile, dict):
        raise ValueError("profile is required")
    _version(profile.get("version"), "profile.version")
    points = profile.get("data_points")
    if not isinstance(points, list) or bool(profile.get("supports_telemetry")) != bool(points):
        raise ValueError("supports_telemetry conflicts with data_points")
    codes = set()
    for point in points:
        code = point.get("code") or point.get("data_point") or point.get("data_point_code")
        if not code or code in codes:
            raise ValueError("data point code is missing or duplicated")
        codes.add(code)
        if point.get("data_type") == "string" and not point.get("allowed_values"):
            raise ValueError("string data point requires allowed_values")
    event_codes = set()
    for event in profile.get("event_types") or []:
        code = event if isinstance(event, str) else (
            event.get("event_type_code") or event.get("event_type") or event.get("code"))
        if not code:
            raise ValueError("event_type_code is required")
        event_codes.add(code)
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")
    for rule in rules:
        _version(rule.get("version"), "rule.version")
        if rule.get("scope_type", "Device").upper() != "DEVICE" or rule.get("scope_ref") != expected_device_id:
            raise ValueError("rule scope does not match device")
        if not isinstance(rule.get("active"), bool):
            raise ValueError("rule active must be boolean")
        if rule.get("threshold") is not None and (isinstance(rule["threshold"], bool)
                                                   or not isinstance(rule["threshold"], (int, float))):
            raise ValueError("threshold must be numeric")
        if rule.get("data_point") and rule["data_point"] not in codes:
            raise ValueError("rule references an unknown data point")
        if rule.get("event_type") and rule["event_type"] not in event_codes:
            raise ValueError("rule references an unknown event_type_code")
        if rule.get("rule_type") == "STATE_CHANGE":
            schedule = rule.get("schedule") or {}
            b_start = rule.get("business_start", schedule.get("business_start"))
            b_end = rule.get("business_end", schedule.get("business_end"))
            tz = rule.get("schedule_timezone", schedule.get("timezone"))
            if b_start or b_end or tz:
                if not (b_start and b_end and tz):
                    raise ValueError("STATE_CHANGE rule schedule must specify start, end, and timezone")
                try:
                    wall_time.fromisoformat(b_start)
                    wall_time.fromisoformat(b_end)
                    ZoneInfo(tz)
                except (ValueError, ZoneInfoNotFoundError) as exc:
                    raise ValueError(f"invalid schedule on rule {rule.get('rule_id')}") from exc
    return config


def validate_status_payload(payload, expected_device_id):
    _identity(payload, expected_device_id)
    if payload.get("state") not in ("online", "offline"):
        raise ValueError("status state must be online or offline")
    return payload


def validate_alarm_payload(payload, expected_device_id):
    _identity(payload, expected_device_id)
    if payload.get("kind") not in ("occurrence", "cleared"):
        raise ValueError("alarm kind is invalid")
    if payload.get("alarm_key") != payload.get("episode_key"):
        raise ValueError("alarm_key and episode_key must match")
    _version(payload.get("rule_version"), "rule_version")
    seq = payload.get("occurrence_seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise ValueError("occurrence_seq must be a positive integer")
    if payload.get("kind") == "occurrence":
        if not payload.get("raised_at"):
            raise ValueError("occurrence alarm must have raised_at")
    elif payload.get("kind") == "cleared":
        if not payload.get("cleared_at"):
            raise ValueError("cleared alarm must have cleared_at")
    if not isinstance(payload.get("event"), dict):
        raise ValueError("alarm payload must contain source event dict")
    return payload


def validate_alarm_ack(message, expected_alarm_key=None, is_duplicate=False):
    """Validate Dev 1's response.message for alarm_controller.receive_alarm."""
    if not isinstance(message, dict):
        raise ValueError("alarm ACK message must be a dictionary")
    if expected_alarm_key and message.get("alarm_key") != expected_alarm_key:
        raise ValueError("alarm_key in ACK does not match expected")
    duplicate = message.get("duplicate")
    if not isinstance(duplicate, bool):
        raise ValueError("ACK duplicate field must be boolean")
    if duplicate != is_duplicate:
        raise ValueError(f"ACK duplicate mismatch: expected {is_duplicate}, got {duplicate}")
    if duplicate is True:
        if message.get("changed") is not False:
            raise ValueError("duplicate alarm ACK must have changed=False")
    else:
        if message.get("changed") is not True:
            raise ValueError("first occurrence alarm ACK must have changed=True")
        if not isinstance(message.get("status"), str):
            raise ValueError("first occurrence alarm ACK must have status string")
    count = message.get("occurrence_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("occurrence_count must be a positive integer")
    return message


def validate_command_claim_response(response_message, expected_device_id=None):
    """Validate response from command_controller.claim_command."""
    if not isinstance(response_message, dict):
        raise ValueError("claim_command message must be a dictionary")
    cmd = response_message.get("command")
    if cmd is None:
        return response_message
    if not isinstance(cmd, dict):
        raise ValueError("command must be null or an object")
    for req in ("command_id", "device_id", "command_code", "delivery_token"):
        if not cmd.get(req):
            raise ValueError(f"command is missing required field '{req}'")
    if expected_device_id and cmd.get("device_id") != expected_device_id:
        raise ValueError("command device_id mismatch")
    return response_message


def validate_command_ack_response(response_message, expected_command_id=None):
    """Validate response from command_controller.ack_command."""
    if not isinstance(response_message, dict):
        raise ValueError("ack_command message must be a dictionary")
    for req in ("command_id", "status"):
        if not response_message.get(req):
            raise ValueError(f"ack_command message missing field '{req}'")
    if expected_command_id and response_message.get("command_id") != expected_command_id:
        raise ValueError("command_id mismatch in ACK response")
    if response_message.get("status") not in ("Succeeded", "Failed"):
        raise ValueError("ack status must be Succeeded or Failed")
    if not isinstance(response_message.get("duplicate"), bool):
        raise ValueError("duplicate field must be boolean")
    return response_message


def validate_dev1_to_dev2_sync(resource_type, payload):
    """Validate Dev 1 -> Dev 2 PUT sync payloads."""
    if not isinstance(payload, dict):
        raise ValueError("sync payload must be an object")
    rtype = resource_type.upper()
    if rtype == "ORGANIZATION":
        if not payload.get("org_code") or not payload.get("name"):
            raise ValueError("Organization requires org_code and name")
    elif rtype == "SITE":
        if not payload.get("org_code") or not payload.get("site_code") or not payload.get("name"):
            raise ValueError("Site requires org_code, site_code, and name")
    elif rtype == "ZONE":
        if not all(payload.get(k) for k in ("org_code", "site_code", "zone_code", "name")):
            raise ValueError("Zone requires org_code, site_code, zone_code, and name")
    elif rtype == "PROFILE":
        if not payload.get("profile_code"):
            raise ValueError("Profile requires profile_code")
        _version(payload.get("version"), "Profile version")
        if not isinstance(payload.get("data_points"), list) or not isinstance(payload.get("event_types"), list):
            raise ValueError("Profile requires data_points and event_types lists")
    elif rtype == "DEVICE":
        if not all(payload.get(k) for k in ("device_id", "org_code", "site_code", "profile_code")):
            raise ValueError("Device requires device_id, org_code, site_code, and profile_code")
        _version(payload.get("config_version"), "Device config_version")
        if payload.get("status") not in ("Active", "Registered", "Maintenance", "Retired", "active", "registered"):
            raise ValueError("Device has invalid status")
    elif rtype == "RULE":
        if not all(payload.get(k) for k in ("rule_id", "org_code", "rule_type")):
            raise ValueError("Rule requires rule_id, org_code, and rule_type")
        _version(payload.get("version"), "Rule version")
        scope = payload.get("scope")
        if not isinstance(scope, dict) or not scope.get("type") or not scope.get("ref"):
            raise ValueError("Rule requires scope with type and ref")
        if not isinstance(payload.get("active"), bool):
            raise ValueError("Rule active must be boolean")
    else:
        raise ValueError(f"unknown sync resource type: {resource_type}")
    return payload


def validate_if06_callback(payload, expected_alarm_key=None):
    """Validate Dev 1 -> Dev 2 POST IF-06 alarm lifecycle callback payload."""
    if not isinstance(payload, dict):
        raise ValueError("IF-06 callback payload must be an object")
    for req in ("alarm_key", "org_code", "state", "version", "changed_at", "changed_by"):
        if payload.get(req) is None or payload.get(req) == "":
            raise ValueError(f"IF-06 callback missing required field '{req}'")
    if expected_alarm_key and payload.get("alarm_key") != expected_alarm_key:
        raise ValueError("IF-06 callback alarm_key mismatch")
    if payload.get("state") not in ("acknowledged", "in_progress", "closed"):
        raise ValueError(f"invalid IF-06 lifecycle state: {payload.get('state')}")
    _version(payload.get("version"), "IF-06 version")
    return payload


def _identity(payload, expected_device_id):
    if not isinstance(payload, dict) or payload.get("device_id") != expected_device_id:
        raise ValueError("payload device identity mismatch")
    if not payload.get("event_id") or not payload.get("org_code"):
        raise ValueError("event_id and org_code are required")


def _version(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be an integer >= 1")
