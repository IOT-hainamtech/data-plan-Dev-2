"""Read-only client for Dev 1's actual Frappe device-config endpoint."""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from datetime import time as wall_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from integrations.frappe_client import validate_and_normalize_credentials
from registry.sync import apply_device, apply_profile, apply_rule


class Dev1ConfigClient:
    def __init__(self, url, timeout, token=None, site_name=None, *,
                 api_key=None, api_secret=None, allow_insecure_lab=False,
                 opener=urllib.request.urlopen):
        if not url.startswith("https://") and not allow_insecure_lab:
            raise ValueError("Dev 1 configuration URL must use HTTPS")
        self.api_key, self.api_secret, self.token = validate_and_normalize_credentials(
            token, api_key=api_key, api_secret=api_secret)
        if not site_name or any(c in site_name for c in "\r\n"):
            raise ValueError("Frappe site name is required")
        self.url, self.timeout = url, timeout
        self.site_name, self.opener = site_name, opener

    def __repr__(self):
        return f"<Dev1ConfigClient url={self.url!r} site={self.site_name!r}>"

    def get_device_config(self, external_device_id):
        if not external_device_id or any(c in external_device_id for c in "\r\n"):
            raise ValueError("external device ID is required")
        separator = "&" if "?" in self.url else "?"
        url = self.url + separator + urllib.parse.urlencode({"device_id": external_device_id})
        request = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"token {self.token}",
            "X-Frappe-Site-Name": self.site_name,
            "Accept": "application/json",
        })
        context = ssl.create_default_context() if url.startswith("https://") else None
        try:
            with self.opener(request, timeout=self.timeout, context=context) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        if status != 200:
            raise RuntimeError(f"Dev 1 configuration request failed with HTTP {status}")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Dev 1 returned invalid JSON") from exc
        config = envelope.get("message") if isinstance(envelope, dict) else None
        if not isinstance(config, dict) or not isinstance(config.get("device"), dict):
            raise ValueError("Dev 1 response is missing message.device")
        device = config["device"]
        if device.get("device_id") != external_device_id:
            raise ValueError("Dev 1 response device does not match request")
        version = device.get("config_version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("Dev 1 response has invalid config_version")
        if not isinstance(config.get("profile"), dict) or not isinstance(config.get("rules"), list):
            raise ValueError("Dev 1 response is missing profile or rules")
        return config


def resolve_confirmed_mapping(cur, entity_type, internal_id, system_code="nextgrp"):
    """Return only an explicitly confirmed, enabled mapping; never guess an ID."""
    cur.execute("""SELECT external_id,config_version FROM integration_identity_mappings
        WHERE system_code=%s AND entity_type=%s AND internal_id=%s
          AND enabled=TRUE AND confirmed=TRUE""",
        (system_code, entity_type, internal_id))
    row = cur.fetchone()
    if not row:
        raise LookupError(f"no confirmed {entity_type} mapping for {internal_id}")
    return row


def resolve_internal_mapping(cur, entity_type, external_id, system_code="nextgrp"):
    """Reverse lookup is also explicit; reconciliation never invents Dev 2 IDs."""
    cur.execute("""SELECT internal_id,config_version FROM integration_identity_mappings
        WHERE system_code=%s AND entity_type=%s AND external_id=%s
          AND enabled=TRUE AND confirmed=TRUE""",
        (system_code, entity_type, external_id))
    row = cur.fetchone()
    if not row:
        raise LookupError(f"no confirmed {entity_type} mapping for external ID {external_id}")
    return row


def resolve_internal_alarm_key(cur, callback_alarm_key):
    """Resolve an IF-06 key without guessing across the Dev 1/Dev 2 boundary.

    Internal keys remain valid for backwards compatibility.  A transformed Dev 1
    key is accepted only when a frozen outbound payload proves a unique mapping.
    """
    cur.execute("SELECT wire_alarm_key FROM alarm_episode_mappings WHERE wire_alarm_key=%s",
                (callback_alarm_key,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""SELECT DISTINCT wire_alarm_key FROM alarm_outbox
        WHERE payload_json->>'alarm_key'=%s AND wire_alarm_key IS NOT NULL
        LIMIT 2""", (callback_alarm_key,))
    rows = cur.fetchall()
    if not rows:
        raise LookupError("alarm callback key has no confirmed outbound mapping")
    if len(rows) != 1:
        raise ValueError("alarm callback key maps to multiple internal episodes")
    return rows[0][0]


def reconcile_device_config(cur, config, system_code="nextgrp"):
    """Atomically apply one actual get_device_config response using confirmed mappings.

    The caller owns the transaction so Profile, Device and all Rules commit together.
    """
    device, profile, site = config["device"], config["profile"], config.get("site") or {}
    external_org = site.get("organization")
    if not external_org:
        raise ValueError("Dev 1 config is missing site.organization")
    org, _ = resolve_internal_mapping(cur, "ORGANIZATION", external_org, system_code)
    site_code, _ = resolve_internal_mapping(cur, "SITE", device["site"], system_code)
    device_id, _ = resolve_internal_mapping(cur, "DEVICE", device["device_id"], system_code)
    profile_code, _ = resolve_internal_mapping(cur, "PROFILE", profile["profile_code"], system_code)
    zone_external = device.get("zone")
    if zone_external:
        zone_code, _ = resolve_internal_mapping(cur, "ZONE", zone_external, system_code)
    else:
        cur.execute("SELECT zone_code FROM devices WHERE device_id=%s AND org_code=%s", (device_id, org))
        row = cur.fetchone()
        if not row or not row[0]:
            try:
                zone_code, _ = resolve_internal_mapping(cur, "ZONE", "DEFAULT", system_code)
            except LookupError:
                raise LookupError("Dev 1 returned no Zone and Dev 2 has no confirmed current Zone")
        else:
            zone_code = row[0]
    points = []
    for point in profile.get("data_points") or []:
        code = point.get("code") or point.get("data_point") or point.get("data_point_code")
        if not code:
            raise ValueError("Dev 1 Profile data point is missing its code")
        points.append({"code": code, "channel": "telemetry",
            "data_type": point.get("data_type", "number"), "unit": point.get("unit"),
            "min_value": point.get("min"), "max_value": point.get("max"),
            "allowed_values": point.get("allowed_values"),
            "required": bool(point.get("required", True)),
            "precision": point.get("precision"), "metadata": {"label": point.get("label", code)}})
    if bool(profile.get("supports_telemetry")) != bool(points):
        raise ValueError("Dev 1 supports_telemetry conflicts with Profile data_points")
    events = []
    for event in profile.get("event_types") or []:
        item = {"event_type": event} if isinstance(event, str) else {
            "event_type": event.get("code") or event.get("event_type") or event.get("event_type_code"),
            "state_point": event.get("state_point"), "state_value": event.get("state_value")}
        if not item["event_type"]:
            raise ValueError("Dev 1 Profile event type is missing its code")
        events.append(item)
    profile_data = {"profile_code": profile_code, "version": profile["version"],
        "display_name": profile.get("profile_name", profile_code), "enabled": True,
        "metadata": {"signal_model": "TELEMETRY" if points else "EVENT",
            "protocol": profile.get("protocol", "MQTT"),
            "expected_interval_s": profile.get("expected_interval_s", 60),
            "heartbeat_interval_s": profile.get("heartbeat_interval_s", 60),
            "offline_after_missed": profile.get("offline_after_missed", 2)},
        "data_points": points, "event_types": events}
    results = [apply_profile(cur, profile_data)]
    device_data = {"device_id": device_id, "org_code": org, "site_code": site_code,
        "zone_code": zone_code, "profile_code": profile_code,
        "enabled": device.get("status", "").lower() == "active",
        "connectivity": device.get("connectivity_type", "WIFI"),
        "version": device["config_version"], "credential_ref": None,
        "telemetry_interval_seconds": profile_data["metadata"]["expected_interval_s"],
        "heartbeat_interval_seconds": profile_data["metadata"]["heartbeat_interval_s"],
        "metadata": {"external_device_id": device["device_id"]}}
    results.append(apply_device(cur, device_data))
    for rule in config.get("rules") or []:
        rule_id, _ = resolve_internal_mapping(cur, "RULE", rule["rule_id"], system_code)
        scope_type = str(rule.get("scope_type", "Device")).upper()
        if scope_type != "DEVICE" or rule.get("scope_ref") != device["device_id"]:
            raise ValueError("get_device_config Rule must be scoped to the requested Device")
        schedule = rule.get("schedule") or {}
        business_start = rule.get("business_start", schedule.get("business_start"))
        business_end = rule.get("business_end", schedule.get("business_end"))
        schedule_timezone = rule.get("schedule_timezone", schedule.get("timezone"))
        if rule["rule_type"] == "STATE_CHANGE":
            if not all((business_start, business_end, schedule_timezone)):
                raise ValueError("Dev 1 S6 Rule requires business_start, business_end and schedule_timezone")
            try:
                wall_time.fromisoformat(business_start)
                wall_time.fromisoformat(business_end)
                ZoneInfo(schedule_timezone)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise ValueError("Dev 1 S6 Rule has an invalid business schedule") from exc
        rule_data = {"rule_id": rule_id, "version": rule["version"], "org_code": org,
            "rule_kind": rule["rule_type"], "scope": {"type": "DEVICE", "device_id": device_id},
            "metric": rule.get("data_point"), "operator": rule.get("operator"),
            "threshold": rule.get("threshold"), "duration_s": rule.get("duration_s", 0),
            "hysteresis": rule.get("hysteresis", 0),
            "clear_duration_s": rule.get("clear_duration_s", 0),
            "quiet_window_s": rule.get("quiet_window_s", 0),
            "event_type": rule.get("event_type"), "open_limit_s": rule.get("open_limit_s"),
            "business_start": business_start, "business_end": business_end,
            "timezone": schedule_timezone,
            "severity": rule.get("severity", "High"), "auto_close": bool(rule.get("auto_close")),
            "enabled": bool(rule.get("active", True))}
        results.append(apply_rule(cur, rule_data))
    return results


def map_alarm_payload(cur, payload, system_code="nextgrp"):
    """Translate an Alarm using confirmed mappings; missing mappings fail closed."""
    # Once the occurrence payload was frozen, CLEARED must reuse the exact same
    # external episode identity even if mappings changed in the meantime.
    prior = None
    if payload.get("kind") == "cleared":
        cur.execute("""SELECT payload_json FROM alarm_outbox
            WHERE wire_alarm_key=%s AND payload_json->>'kind'='occurrence'
              AND payload_json->>'alarm_key'<>%s
            ORDER BY id LIMIT 1""", (payload["alarm_key"], payload["alarm_key"]))
        prior = cur.fetchone()
    if prior:
        frozen = prior[0]
        mapped = deepcopy(payload)
        mapped.update({key: frozen.get(key) for key in
            ("org_code", "device_id", "rule_id", "rule_version", "zone_code",
             "alarm_key", "episode_key")})
        return mapped
    mapped = deepcopy(payload)
    org, _ = resolve_confirmed_mapping(cur, "ORGANIZATION", payload["org_code"], system_code)
    device, _ = resolve_confirmed_mapping(cur, "DEVICE", payload["device_id"], system_code)
    rule, rule_version = resolve_confirmed_mapping(cur, "RULE", payload["rule_id"], system_code)
    if rule_version < 1:
        raise ValueError("confirmed Rule mapping must carry Dev 1 version >= 1")
    zone = payload.get("zone_code")
    if zone:
        zone, _ = resolve_confirmed_mapping(cur, "ZONE", zone, system_code)
    started = datetime.fromisoformat(payload["episode_start"].replace("Z", "+00:00"))
    epoch = int(started.astimezone(timezone.utc).timestamp())
    external_key = f"{rule}:{device}:{epoch}"
    mapped.update({"org_code": org, "device_id": device, "rule_id": rule,
                   "rule_version": rule_version, "zone_code": zone,
                   "alarm_key": external_key, "episode_key": external_key})
    return mapped
