"""Tenant-authenticated Data API; terminate TLS at the trusted LAN proxy if exposed.

Not the finalized IF-01..IF-07 contract; do not expose to the internet.
"""
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from datetime import datetime, timezone

from core.auth import service_context
from core.database import db_connection
from api.integration import router as integration_router

app = FastAPI(title="nextWireless L2A Data Plane (local PoC)", version="0.1.0")


tenant_org = service_context

app.include_router(integration_router)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@app.get("/api/v1/health")
def health():
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"service": "nextwireless-data-api", "status": "UP"}
    except Exception:
        return JSONResponse(status_code=503, content={"service": "nextwireless-data-api", "status": "DOWN"})


@app.get("/api/v1/outbox/metrics")
def outbox_metrics(org_code: str = Depends(tenant_org)):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT
                count(*) FILTER (WHERE o.delivery_state IN ('PENDING','IN_FLIGHT')),
                count(*) FILTER (WHERE o.delivery_state='FAILED'),
                COALESCE(sum(o.failure_count),0),
                min(o.created_at) FILTER (WHERE o.delivery_state IN ('PENDING','IN_FLIGHT'))
                FROM alarm_outbox o
                JOIN alarm_occurrences a ON a.alarm_key=o.alarm_key
                JOIN devices d ON d.device_id=a.device_id
                WHERE d.org_code=%s""", (org_code,))
            pending, failed, failures, oldest = cur.fetchone()
    age = max(0.0, (utc_now() - oldest).total_seconds()) if oldest else 0.0
    return {"pending": pending, "failed": failed, "delivery_failures": failures,
            "oldest_pending_age_seconds": round(age, 3)}


@app.get("/api/v1/devices/{device_id}/latest")
def latest(device_id: str, org_code: str = Depends(tenant_org)):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT device_id FROM devices WHERE device_id=%s AND org_code=%s AND enabled=TRUE", (device_id, org_code))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Device not found")
            cur.execute("""SELECT DISTINCT ON (metric) metric, value_number, measured_at
                FROM telemetry WHERE device_id=%s ORDER BY metric, measured_at DESC""", (device_id,))
            rows = cur.fetchall()
            cur.execute("""SELECT event_id,event_type,event_at,payload_json
                FROM device_events WHERE device_id=%s ORDER BY event_at DESC LIMIT 1""", (device_id,))
            event = cur.fetchone()
    latest_event = None
    if event:
        data = (event["payload_json"] or {}).get("data", {})
        latest_event = {"event_id": event["event_id"], "event_type": event["event_type"],
                        "event_at": event["event_at"].isoformat() + "Z",
                        "door_state": data.get("door_state")}
    return {"device_id": device_id, "metrics": [
        {"metric": r["metric"], "value": r["value_number"],
         "measured_at": r["measured_at"].isoformat() + "Z"} for r in rows],
         "latest_event": latest_event}


@app.get("/api/v1/devices/{device_id}/telemetry")
def telemetry(device_id: str, metric: str | None = None,
              limit: int = Query(default=100, ge=1, le=1000),
              offset: int = Query(default=0, ge=0),
              org_code: str = Depends(tenant_org)):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT device_id FROM devices WHERE device_id=%s AND org_code=%s AND enabled=TRUE", (device_id, org_code))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Device not found")
            cur.execute("""SELECT count(*) FROM telemetry
                WHERE device_id=%s AND (%s::text IS NULL OR metric=%s)""", (device_id, metric, metric))
            total = cur.fetchone()["count"]
            cur.execute("""SELECT metric, value_number, measured_at FROM telemetry
                WHERE device_id=%s AND (%s::text IS NULL OR metric=%s)
                ORDER BY measured_at DESC LIMIT %s OFFSET %s""", (device_id, metric, metric, limit, offset))
            rows = cur.fetchall()
    return {"device_id": device_id, "total": total, "limit": limit, "offset": offset, "points": [
        {"metric": r["metric"], "value": r["value_number"],
         "measured_at": r["measured_at"].isoformat() + "Z"} for r in rows]}


@app.get("/api/v1/devices")
def list_devices(org_code: str = Depends(tenant_org),
                 limit: int = Query(default=100, ge=1, le=500),
                 offset: int = Query(default=0, ge=0),
                 ):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT count(*) FROM devices WHERE org_code=%s", (org_code,))
            total = cur.fetchone()["count"]
            cur.execute("""SELECT d.device_id, d.org_code, d.site_code, d.zone_code,
                d.profile_code, d.telemetry_interval_seconds, d.heartbeat_interval_seconds, d.enabled,
                s.current_state, s.received_at AS status_updated_at, s.last_seen_at, s.boot_id
                FROM devices d
                LEFT JOIN device_status s ON d.device_id = s.device_id
                WHERE d.org_code=%s ORDER BY d.device_id LIMIT %s OFFSET %s""", (org_code, limit, offset))
            rows = cur.fetchall()
    return {"total": total, "limit": limit, "offset": offset, "devices": [
        {"device_id": r["device_id"], "org_code": r["org_code"], "site_code": r["site_code"],
         "zone_code": r["zone_code"], "profile_code": r["profile_code"],
         "telemetry_interval_seconds": r["telemetry_interval_seconds"],
         "heartbeat_interval_seconds": r["heartbeat_interval_seconds"], "enabled": r["enabled"],
         "current_state": r["current_state"] or "offline",
         "status_updated_at": r["status_updated_at"].isoformat() + "Z" if r["status_updated_at"] else None,
         "last_seen_at": r["last_seen_at"].isoformat() + "Z" if r["last_seen_at"] else None,
         "boot_id": r["boot_id"]}
        for r in rows
    ]}
@app.get("/api/v1/zones/{zone_code}/summary")
def zone_summary(zone_code: str, site_code: str = Query(...), org_code: str = Depends(tenant_org)):
    """Site is required because zone_code is only unique within an organization/site."""
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT z.display_name,count(d.device_id) AS device_count,
                count(d.device_id) FILTER (WHERE s.current_state='online') AS online_count,
                count(d.device_id) FILTER (WHERE s.current_state='offline' OR s.current_state IS NULL) AS offline_count
                FROM zones z LEFT JOIN devices d ON (d.org_code,d.site_code,d.zone_code)=(z.org_code,z.site_code,z.zone_code)
                LEFT JOIN device_status s ON s.device_id=d.device_id
                WHERE z.org_code=%s AND z.site_code=%s AND z.zone_code=%s
                GROUP BY z.display_name""",(org_code,site_code,zone_code)); row=cur.fetchone()
    if not row: raise HTTPException(404,"Zone not found")
    return {"org_code":org_code,"site_code":site_code,"zone_code":zone_code,**row}
@app.get("/api/v1/devices/{device_id}/status")
def device_status_detail(device_id: str, history_limit: int = Query(default=20, ge=1, le=100),
                         org_code: str = Depends(tenant_org)):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT device_id FROM devices WHERE device_id=%s AND org_code=%s AND enabled=TRUE", (device_id, org_code))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Device not found")
            cur.execute("""SELECT current_state, received_at, last_seen_at, timeout_seconds, boot_id
                FROM device_status WHERE device_id=%s""", (device_id,))
            status_row = cur.fetchone()
            cur.execute("""SELECT state, received_at, boot_id
                FROM device_status_history WHERE device_id=%s
                ORDER BY received_at DESC LIMIT %s""", (device_id, history_limit))
            history_rows = cur.fetchall()
    return {
        "device_id": device_id,
        "current_state": status_row["current_state"] if status_row else "offline",
        "last_seen_at": status_row["last_seen_at"].isoformat() + "Z" if status_row and status_row["last_seen_at"] else None,
        "status_updated_at": status_row["received_at"].isoformat() + "Z" if status_row and status_row["received_at"] else None,
        "timeout_seconds": status_row["timeout_seconds"] if status_row else 120,
        "history": [
            {"state": h["state"], "ts": h["received_at"].isoformat() + "Z", "boot_id": h["boot_id"]}
            for h in history_rows
        ]
    }


@app.get("/api/v1/devices/{device_id}/events")
def device_events(device_id: str, limit: int = Query(default=50, ge=1, le=500),
                  offset: int = Query(default=0, ge=0),
                  org_code: str = Depends(tenant_org)):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT device_id FROM devices WHERE device_id=%s AND org_code=%s AND enabled=TRUE", (device_id, org_code))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Device not found")
            cur.execute("SELECT count(*) FROM device_events WHERE device_id=%s", (device_id,))
            total = cur.fetchone()["count"]
            cur.execute("""SELECT event_id, event_type, boot_id, seq, event_at, received_at, payload_json
                FROM device_events WHERE device_id=%s
                ORDER BY event_at DESC LIMIT %s OFFSET %s""", (device_id, limit, offset))
            rows = cur.fetchall()
    return {"device_id": device_id, "total": total, "limit": limit, "offset": offset, "events": [
        {"event_id": r["event_id"], "event_type": r["event_type"], "boot_id": r["boot_id"],
         "seq": r["seq"], "event_at": r["event_at"].isoformat() + "Z",
         "received_at": r["received_at"].isoformat() + "Z", "payload": r["payload_json"]}
        for r in rows
    ]}
@app.get("/api/v1/alarms")
def list_alarms(limit: int = Query(default=50, ge=1, le=500),
                offset: int = Query(default=0, ge=0),
                org_code: str = Depends(tenant_org)):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT count(*) FROM alarm_occurrences a
                JOIN devices d ON d.device_id=a.device_id AND d.org_code=%s""", (org_code,))
            total = cur.fetchone()["count"]
            cur.execute("""SELECT a.alarm_key, a.episode_key, a.rule_code, a.device_id, a.severity,
                a.occurrence_kind, a.event_type, a.triggered_at, a.cleared_at, a.payload_json,
                o.delivery_state, o.attempt_count, o.ack_id, o.delivered_at,l.state AS lifecycle_state
                FROM alarm_occurrences a
                JOIN devices d ON d.device_id=a.device_id AND d.org_code=%s
                LEFT JOIN alarm_outbox o ON a.alarm_key = o.alarm_key
                LEFT JOIN alarm_lifecycle l ON l.wire_alarm_key=a.payload_json->>'alarm_key'
                ORDER BY a.triggered_at DESC LIMIT %s OFFSET %s""", (org_code, limit, offset))
            rows = cur.fetchall()
    return {"total": total, "limit": limit, "offset": offset, "alarms": [
        {"alarm_key": r["alarm_key"], "episode_key": r["episode_key"], "rule_code": r["rule_code"],
         "device_id": r["device_id"], "severity": r["severity"], "occurrence_kind": r["occurrence_kind"],
         "event_type": r["event_type"], "triggered_at": r["triggered_at"].isoformat() + "Z",
         "raised_at": r["payload_json"].get("raised_at"),
         "cleared_at": r["cleared_at"].isoformat() + "Z" if r["cleared_at"] else None,
         "status": r["lifecycle_state"] or ("cleared" if r["occurrence_kind"] == "CLEARED" else "open"),
         "summary": r["payload_json"].get("event", {}).get("data", {}).get("summary") or r["event_type"],
         "delivery_state": r["delivery_state"] or "UNKNOWN", "attempt_count": r["attempt_count"],
         "ack_id": r["ack_id"], "delivered_at": r["delivered_at"].isoformat() + "Z" if r["delivered_at"] else None}
        for r in rows
    ]}
# The package-level compatibility entrypoint exports this ASGI application.
