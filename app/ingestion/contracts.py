"""Transport-independent MQTT topic and payload validation."""
from datetime import datetime, timezone

from core.clock import now_utc


def require_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def parse_timestamp(value):
    if value is None:
        return now_utc(), "server"
    if not isinstance(value, str):
        raise ValueError("ts must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ts") from exc
    if parsed.tzinfo is None:
        raise ValueError("ts must include timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None), "device"


def validate_telemetry_data(payload, point_definitions=None):
    data = payload["data"]
    definitions = point_definitions or {
        "temperature": {"data_type": "number", "min_value": -20, "max_value": 80},
        "humidity": {"data_type": "number", "min_value": 0, "max_value": 100},
    }
    quality = payload.get("quality", "good")
    if quality not in ("good", "error"):
        raise ValueError("quality must be good or error")
    if not set(data).issubset(definitions):
        raise ValueError("unsupported telemetry metric")
    if quality == "error":
        if data:
            raise ValueError("error telemetry must not contain measurements")
        return quality, data
    if not data:
        raise ValueError("good telemetry requires measurements")
    missing = {name for name, definition in definitions.items()
               if definition.get("required") and name not in data}
    if missing:
        raise ValueError(f"required telemetry missing: {','.join(sorted(missing))}")
    for metric, value in data.items():
        definition = definitions[metric]
        if definition["data_type"] == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{metric} must be numeric")
            if not (float("-inf") < value < float("inf")):
                raise ValueError("non-finite metric")
            minimum, maximum = definition.get("min_value"), definition.get("max_value")
            if minimum is not None and value < minimum or maximum is not None and value > maximum:
                raise ValueError(f"{metric} out of range")
    return quality, data


def door_heartbeat_event(profile, body, event_types=None):
    door_state = body["data"].get("door_state")
    if door_state is None:
        return None
    mapping = event_types or ({"open": "DOOR_OPENED", "closed": "DOOR_CLOSED"}
                              if profile == "DOOR_SENSOR" else {})
    if door_state not in mapping:
        raise ValueError("invalid door heartbeat")
    return f"heartbeat:{body['boot_id']}:{body['seq']}", mapping[door_state]


def validate_message(topic, payload):
    parts = topic.split("/")
    if len(parts) != 6 or parts[:2] != ["nw", "v1"]:
        raise ValueError("invalid topic")
    _, _, org, site, device, channel = parts
    if channel not in ("telemetry", "event", "status"):
        raise ValueError("unsupported channel")
    if not isinstance(payload, dict):
        raise ValueError("payload must be JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    if payload.get("device_id") != device:
        raise ValueError("topic/device_id mismatch")
    if not isinstance(payload.get("boot_id"), str) or not payload["boot_id"]:
        raise ValueError("boot_id required")
    require_int(payload.get("seq"), "seq")
    if not isinstance(payload.get("data"), dict):
        raise ValueError("data must be JSON object")
    stamp, source = parse_timestamp(payload.get("ts"))
    if channel == "event":
        if not isinstance(payload.get("event_id"), str) or not payload["event_id"]:
            raise ValueError("event_id required")
        if not isinstance(payload.get("event_type"), str) or not payload["event_type"]:
            raise ValueError("event_type required")
    if channel == "status" and payload.get("state") not in ("online", "offline"):
        raise ValueError("status state must be online/offline")
    return org, site, device, channel, stamp, source
