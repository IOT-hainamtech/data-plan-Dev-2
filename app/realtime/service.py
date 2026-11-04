"""Transactional creation of outbound realtime events; network I/O is forbidden here."""
from datetime import timezone

from psycopg.types.json import Jsonb


def _utc(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def enqueue_telemetry_event(cur, *, org_code, device_id, boot_id, seq, measured_at,
                            metrics, point_definitions, created_at):
    event_id = f"{device_id}-{boot_id}-{seq}-telemetry"
    values = []
    for code, value in sorted(metrics.items()):
        unit = (point_definitions.get(code) or {}).get("unit")
        values.append({"code": code, "value": float(value), "unit": unit})
    payload = {"org_code": org_code, "device_id": device_id, "event_id": event_id,
               "event_type": "TELEMETRY_UPDATED", "event_at": _utc(measured_at),
               "metrics": values, "data_status": "LIVE"}
    cur.execute("""INSERT INTO device_event_outbox
        (event_id,device_id,payload_json,next_attempt_at,created_at)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING""",
        (event_id, device_id, Jsonb(payload), created_at, created_at))
    return cur.rowcount


def enqueue_device_event(cur, *, org_code, device_id, event_id, event_type,
                         event_at, door_state, created_at):
    payload = {"org_code": org_code, "device_id": device_id, "event_id": event_id,
               "event_type": event_type, "event_at": _utc(event_at),
               "door_state": door_state, "data_status": "LIVE"}
    cur.execute("""INSERT INTO device_event_outbox
        (event_id,device_id,payload_json,next_attempt_at,created_at)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING""",
        (event_id, device_id, Jsonb(payload), created_at, created_at))
    return cur.rowcount
