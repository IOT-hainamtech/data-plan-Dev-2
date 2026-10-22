"""Transactional device presence operations."""
import logging

LOG = logging.getLogger("data-plane")


def update_device_presence(cur, *, device_id, boot_id, received_at, explicit_state=None):
    cur.execute("SELECT current_state FROM device_status WHERE device_id=%s FOR UPDATE", (device_id,))
    row = cur.fetchone()
    target_state = explicit_state or "online"
    previous = row[0] if row else None
    inserted = 0
    if previous != target_state:
        cur.execute("""INSERT INTO device_status_history
            (device_id,state,received_at,boot_id) VALUES (%s,%s,%s,%s)""",
            (device_id, target_state, received_at, boot_id))
        inserted = 1
    cur.execute("""INSERT INTO device_status
        (device_id,current_state,received_at,boot_id,last_seen_at) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (device_id) DO UPDATE SET current_state=EXCLUDED.current_state,
          received_at=EXCLUDED.received_at,boot_id=EXCLUDED.boot_id,
          last_seen_at=COALESCE(EXCLUDED.last_seen_at,device_status.last_seen_at)""",
        (device_id, target_state, received_at, boot_id,
         received_at if target_state == "online" else None))
    return inserted


def reconcile_device_timeouts(cur, now, device_id=None):
    cur.execute("""SELECT device_id,boot_id,last_seen_at,timeout_seconds FROM device_status
        WHERE current_state='online' AND last_seen_at IS NOT NULL
          AND last_seen_at <= %s - (timeout_seconds || ' seconds')::interval
          AND (%s::text IS NULL OR device_id=%s) FOR UPDATE""", (now, device_id, device_id))
    rows = cur.fetchall()
    for dev_id, boot_id, last_seen, timeout_seconds in rows:
        cur.execute("""INSERT INTO device_status_history(device_id,state,received_at,boot_id)
            VALUES (%s,'offline',%s,%s)""", (dev_id, now, boot_id))
        cur.execute("UPDATE device_status SET current_state='offline',received_at=%s WHERE device_id=%s",
                    (now, dev_id))
        LOG.warning("DEVICE_OFFLINE_TIMEOUT device=%s last_seen=%s age=%.1fs timeout=%ds",
                    dev_id, last_seen, (now-last_seen).total_seconds(), timeout_seconds)
    return len(rows)
