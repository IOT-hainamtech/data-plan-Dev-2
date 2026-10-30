"""Transactional device presence operations."""
import logging
import uuid
from datetime import timezone
from psycopg.types.json import Jsonb

LOG = logging.getLogger("data-plane")


def enqueue_status_reconciliation(cur, reconciled_at):
    """Queue one full status snapshot per enabled device and five-minute bucket."""
    if reconciled_at.tzinfo is not None:
        reconciled_at = reconciled_at.astimezone(timezone.utc).replace(tzinfo=None)
    bucket = int(reconciled_at.replace(tzinfo=timezone.utc).timestamp()) // 300
    cur.execute("""SELECT d.device_id,d.org_code,s.current_state,s.last_seen_at,
            s.received_at,s.boot_id
        FROM devices d JOIN device_status s ON s.device_id=d.device_id
        WHERE d.enabled=TRUE ORDER BY d.device_id""")
    rows = cur.fetchall()
    queued = 0
    for device_id, org, state, last_seen, status_at, boot_id in rows:
        event_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"nextwireless:status-reconcile:{device_id}:{bucket}"))
        payload = {
            "event_id": event_id, "org_code": org, "device_id": device_id,
            "state": state,
            "since": status_at.isoformat(timespec="milliseconds") + "Z",
            "last_seen": last_seen.isoformat(timespec="milliseconds") + "Z" if last_seen else None,
            "reason": "telemetry" if state == "online" else "timeout",
        }
        cur.execute("""INSERT INTO status_outbox
            (delivery_event_id,device_id,payload_json,next_attempt_at,created_at)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(delivery_event_id) DO NOTHING""",
            (event_id, device_id, Jsonb(payload), reconciled_at, reconciled_at))
        queued += cur.rowcount
    return queued


def update_device_presence(cur, *, device_id, boot_id, received_at, explicit_state=None):
    cur.execute("SELECT current_state,last_seen_at FROM device_status WHERE device_id=%s FOR UPDATE", (device_id,))
    row = cur.fetchone()
    target_state = explicit_state or "online"
    previous = row[0] if row else None
    previous_last_seen = row[1] if row else None
    inserted = 0
    if previous != target_state:
        cur.execute("""INSERT INTO device_status_history
            (device_id,state,received_at,boot_id,previous_state,reason)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (device_id, target_state, received_at, boot_id, previous,
             "lwt" if explicit_state == "offline" else "connect"))
        inserted = 1
    cur.execute("""INSERT INTO device_status
        (device_id,current_state,received_at,boot_id,last_seen_at) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (device_id) DO UPDATE SET current_state=EXCLUDED.current_state,
          received_at=EXCLUDED.received_at,boot_id=EXCLUDED.boot_id,
          last_seen_at=COALESCE(EXCLUDED.last_seen_at,device_status.last_seen_at)""",
        (device_id, target_state, received_at, boot_id,
         received_at if target_state == "online" else None))
    if previous != target_state:
        cur.execute("SELECT org_code FROM devices WHERE device_id=%s",(device_id,))
        org=cur.fetchone()[0]; event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"nextwireless:status:{device_id}:{target_state}:{received_at.isoformat()}"))
        last_seen=received_at if target_state=="online" else previous_last_seen
        payload={"event_id":event_id,"org_code":org,"device_id":device_id,"state":target_state,
                 "since":received_at.isoformat(timespec="milliseconds")+"Z",
                 "last_seen":last_seen.isoformat(timespec="milliseconds")+"Z" if last_seen else None,
                 "reason":"lwt" if explicit_state=="offline" else
                          ("connect" if explicit_state=="online" else "telemetry")}
        cur.execute("""INSERT INTO status_outbox(delivery_event_id,device_id,payload_json,next_attempt_at,created_at)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(delivery_event_id) DO NOTHING""",
            (event_id,device_id,Jsonb(payload),received_at,received_at))
    return inserted


def reconcile_device_timeouts(cur, now, device_id=None):
    cur.execute("""SELECT device_id,boot_id,last_seen_at,timeout_seconds FROM device_status
        WHERE current_state='online' AND last_seen_at IS NOT NULL
          AND last_seen_at <= %s - (timeout_seconds || ' seconds')::interval
          AND (%s::text IS NULL OR device_id=%s) FOR UPDATE""", (now, device_id, device_id))
    rows = cur.fetchall()
    for dev_id, boot_id, last_seen, timeout_seconds in rows:
        cur.execute("""INSERT INTO device_status_history
            (device_id,state,received_at,boot_id,previous_state,reason)
            VALUES (%s,'offline',%s,%s,'online','timeout')""", (dev_id, now, boot_id))
        cur.execute("UPDATE device_status SET current_state='offline',received_at=%s WHERE device_id=%s",
                    (now, dev_id))
        cur.execute("SELECT org_code FROM devices WHERE device_id=%s",(dev_id,))
        org=cur.fetchone()[0]; event_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"nextwireless:status:{dev_id}:offline:{now.isoformat()}"))
        payload={"event_id":event_id,"org_code":org,"device_id":dev_id,"state":"offline",
                 "since":now.isoformat(timespec="milliseconds")+"Z",
                 "last_seen":last_seen.isoformat(timespec="milliseconds")+"Z","reason":"timeout"}
        cur.execute("""INSERT INTO status_outbox(delivery_event_id,device_id,payload_json,next_attempt_at,created_at)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(delivery_event_id) DO NOTHING""",(event_id,dev_id,Jsonb(payload),now,now))
        LOG.warning("DEVICE_OFFLINE_TIMEOUT device=%s last_seen=%s age=%.1fs timeout=%ds",
                    dev_id, last_seen, (now-last_seen).total_seconds(), timeout_seconds)
    return len(rows)
