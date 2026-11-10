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


def _contract_bool(value, field):
    """Accept Frappe's documented 0/1 representation without truthy coercion."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{field} must be boolean or integer 0/1")


def _require_external_id(resource, expected, label):
    external_id = resource.get("external_id")
    if external_id is not None:
        if not isinstance(external_id, str) or not external_id.strip():
            raise ValueError(f"Dev 1 {label} has invalid external_id")
        if external_id != expected:
            raise ValueError(f"Dev 1 {label} external_id does not match confirmed mapping")


def _resolve_rule_event_type(rule_id, kind, event_payload=None, cur=None, runtime_config=None):
    """Resolve confirmed Dev 1 event_type for rule transition without unconfirmed guessing."""
    confirmed_events = {
        ("S1_TEMP_HIGH", "occurrence"): "TEMP_HIGH",
        ("S1_TEMP_HIGH", "cleared"): "TEMP_NORMAL",
        ("S6_DOOR_OPEN", "occurrence"): "DOOR_OPENED",
        ("S6_DOOR_OPEN", "cleared"): "DOOR_CLOSED",
    }
    match = confirmed_events.get((rule_id, kind))
    if match:
        return match

    rule_kind = None
    event_type = None
    metric = None
    if isinstance(runtime_config, str):
        try:
            runtime_config = json.loads(runtime_config)
        except Exception:
            runtime_config = None
    if isinstance(runtime_config, dict):
        rule_kind = runtime_config.get("rule_kind") or runtime_config.get("rule_type")
        event_type = runtime_config.get("event_type")
        metric = runtime_config.get("metric") or runtime_config.get("data_point")
    elif cur:
        try:
            cur.execute("SELECT rule_kind, metric, event_type FROM rule_definitions WHERE rule_code=%s", (rule_id,))
            row = cur.fetchone()
            if row:
                rule_kind, metric, event_type = row
        except Exception:
            pass

    if rule_kind == "STATE_CHANGE" and event_type:
        if kind == "occurrence":
            return event_type
        if event_type == "DOOR_OPENED":
            return "DOOR_CLOSED"
        event_obj = event_payload if isinstance(event_payload, dict) else {}
        if event_obj.get("event_type") and event_obj.get("event_type") != event_type:
            return event_obj["event_type"]
        return f"{event_type}_CLEARED" if not event_type.endswith("_CLEARED") else event_type

    if rule_kind == "THRESHOLD" and metric:
        return f"{metric.upper()}_HIGH" if kind == "occurrence" else f"{metric.upper()}_NORMAL"

    if isinstance(event_payload, dict) and event_payload.get("event_type"):
        return event_payload["event_type"]

    return None


def _protect_runtime_profile(cur, profile_code, profile_data):
    """Fail closed when a snapshot would reinterpret the physical signal model."""
    cur.execute("""SELECT channel,point_code,data_type,unit,min_value,max_value,
            allowed_values,required FROM device_profile_points
        WHERE profile_code=%s ORDER BY channel,point_code""", (profile_code,))
    current_points=cur.fetchall()
    cur.execute("""SELECT event_type,state_point,state_value FROM device_profile_events
        WHERE profile_code=%s ORDER BY event_type""", (profile_code,))
    current_events=cur.fetchall()
    if not current_points and not current_events:
        return
    incoming_points=sorted((p["channel"],p["code"],p["data_type"],p.get("unit"),
        p.get("min_value"),p.get("max_value"),p.get("allowed_values"),p["required"])
        for p in profile_data["data_points"])
    normalized_current=sorted((channel,code,data_type,unit,min_value,max_value,
        list(allowed_values) if allowed_values is not None else None,required)
        for channel,code,data_type,unit,min_value,max_value,allowed_values,required in current_points)
    incoming_events=sorted((e["event_type"],e.get("state_point"),e.get("state_value"))
                           for e in profile_data["event_types"])
    if incoming_points != normalized_current or incoming_events != sorted(current_events):
        raise ValueError("Dev 1 Profile schema conflicts with the protected runtime Profile")


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
    _require_external_id(device, device_id, "Device")
    _require_external_id(profile, profile_code, "Profile")
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
            "required": _contract_bool(point.get("required", 1), "profile.data_points[].required"),
            "precision": point.get("precision"), "metadata": {"label": point.get("label", code)}})
    supports_telemetry=_contract_bool(profile.get("supports_telemetry"),
                                      "profile.supports_telemetry")
    if supports_telemetry != bool(points):
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
    _protect_runtime_profile(cur,profile_code,profile_data)
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
        _require_external_id(rule,rule_id,"Rule")
        scope_type = str(rule.get("scope_type", "Device")).upper()
        if scope_type != "DEVICE" or rule.get("scope_ref") != device["device_id"]:
            raise ValueError("get_device_config Rule must be scoped to the requested Device")
        schedule = rule.get("schedule") or {}
        business_start = rule.get("business_start", schedule.get("business_start"))
        business_end = rule.get("business_end", schedule.get("business_end"))
        schedule_timezone = rule.get("schedule_timezone", schedule.get("timezone"))
        if rule["rule_type"] == "STATE_CHANGE":
            schedule_parts = (business_start, business_end, schedule_timezone)
            if any(schedule_parts):
                if not all(schedule_parts):
                    raise ValueError("Dev 1 S6 Rule has an incomplete business schedule")
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
            "severity": rule.get("severity", "High"),
            "auto_close": _contract_bool(rule.get("auto_close", 0), "rules[].auto_close"),
            "enabled": _contract_bool(rule.get("active", 1), "rules[].active")}
        results.append(apply_rule(cur, rule_data, sync_mapping=True,
                                  mapping_external_id=rule["rule_id"]))
    return results


def map_alarm_payload(cur, payload, system_code="nextgrp"):
    """Translate an Alarm using confirmed mappings; missing mappings fail closed."""
    # Once the occurrence payload was frozen, CLEARED must reuse the exact same
    # external episode identity even if mappings changed in the meantime.
    prior = None
    if payload.get("kind") == "cleared":
        cur.execute("""SELECT payload_json FROM alarm_outbox
            WHERE wire_alarm_key=%s AND payload_json->>'kind'='occurrence'
              AND delivery_state='DELIVERED'
            ORDER BY id LIMIT 1""", (payload["alarm_key"],))
        prior = cur.fetchone()
    if prior:
        frozen = prior[0]
        if payload.get("rule_version") != frozen.get("rule_version"):
            raise ValueError("cleared payload must retain the frozen occurrence Rule version")
        mapped = deepcopy(payload)
        mapped.update({key: frozen.get(key) for key in
            ("org_code", "device_id", "rule_id", "rule_version",
             "alarm_key", "episode_key")})
        mapped.pop("zone_code", None)
        mapped.pop("site_code", None)
        if mapped.get("event"):
            event_type = _resolve_rule_event_type(payload["rule_id"], "cleared", mapped.get("event"), cur=cur)
            if event_type is None:
                raise ValueError("Unconfirmed cleared event_type")
            mapped["event"]["event_type"] = event_type
        return mapped
    mapped = deepcopy(payload)
    episode_mapping=None
    if payload.get("alarm_key"):
        cur.execute("""SELECT frozen_rule_version,frozen_identity_json,episode_provenance
            FROM alarm_episode_mappings WHERE wire_alarm_key=%s""", (payload["alarm_key"],))
        episode_mapping=cur.fetchone()
    if episode_mapping and episode_mapping[2] == "FROZEN":
        frozen_version,identity,_=episode_mapping
        identity=identity or {}
        rule=identity.get("external_rule_id")
        device=identity.get("external_device_id")
        if not rule or not device or payload.get("rule_version") != frozen_version:
            raise ValueError("frozen episode integration identity is incomplete")
        started = datetime.fromisoformat(payload["episode_start"].replace("Z", "+00:00"))
        epoch = int(started.astimezone(timezone.utc).timestamp())
        external_key=f"{rule}:{device}:{epoch}"
        org=identity.get("external_org_code")
        if not org:
            raise ValueError("frozen episode Organization identity is incomplete")
        mapped.update({"org_code":org,"device_id":device,"rule_id":rule,
                       "rule_version":frozen_version,"alarm_key":external_key,
                       "episode_key":external_key})
        mapped.pop("zone_code",None); mapped.pop("site_code",None)
        if mapped.get("event"):
            event_type = _resolve_rule_event_type(payload["rule_id"], payload.get("kind"), mapped.get("event"), cur=cur)
            if event_type is None:
                raise ValueError("Dev 1 event_type is not confirmed for this Rule transition")
            mapped["event"]["event_type"]=event_type
        return mapped
    cur.execute("""SELECT config_version,config_json FROM rule_definitions
        WHERE rule_code=%s AND enabled=TRUE""", (payload["rule_id"],))
    runtime_rule = cur.fetchone()
    if not runtime_rule:
        raise LookupError("enabled runtime Rule is missing")
    runtime_version, runtime_config = runtime_rule
    org, _ = resolve_confirmed_mapping(cur, "ORGANIZATION", payload["org_code"], system_code)
    device, _ = resolve_confirmed_mapping(cur, "DEVICE", payload["device_id"], system_code)
    rule, rule_version = resolve_confirmed_mapping(cur, "RULE", payload["rule_id"], system_code)
    if rule_version < 1:
        raise ValueError("confirmed Rule mapping must carry Dev 1 version >= 1")
    if payload.get("rule_version") != runtime_version or runtime_version != rule_version:
        raise ValueError("runtime, frozen payload and confirmed Dev 1 Rule versions must match")
    started = datetime.fromisoformat(payload["episode_start"].replace("Z", "+00:00"))
    epoch = int(started.astimezone(timezone.utc).timestamp())
    external_key = f"{rule}:{device}:{epoch}"
    mapped.update({"org_code": org, "device_id": device, "rule_id": rule,
                   "rule_version": rule_version,
                   "alarm_key": external_key, "episode_key": external_key})
    mapped.pop("zone_code", None)
    mapped.pop("site_code", None)
    if mapped.get("event"):
        event_type = _resolve_rule_event_type(
            payload["rule_id"], payload.get("kind"), mapped.get("event"), cur=cur, runtime_config=runtime_config
        )
        if event_type is None:
            raise ValueError("Dev 1 event_type is not confirmed for this Rule transition")
        mapped["event"]["event_type"] = event_type
    return mapped
