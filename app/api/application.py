"""Tenant-authenticated Data API; terminate TLS at the trusted LAN proxy if exposed.

Not the finalized IF-01..IF-07 contract; do not expose to the internet.
"""
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
import base64
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.auth import service_context
from core.database import db_connection
from api.integration import router as integration_router

app = FastAPI(title="nextWireless L2A Data Plane (local PoC)", version="0.1.0")


tenant_org = service_context

app.include_router(integration_router)

LOCAL_TIMEZONE_NAME = "Asia/Ho_Chi_Minh"
LOCAL_TIMEZONE = ZoneInfo(LOCAL_TIMEZONE_NAME)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_parameter(value, name):
    if value is None:
        return None
    if value.tzinfo is None:
        raise HTTPException(422, f"{name} must include a UTC offset")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def encode_cursor(measured_at, metric):
    raw = json.dumps([measured_at.isoformat(), metric], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value):
    if value is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        stamp, metric = json.loads(raw)
        return datetime.fromisoformat(stamp), str(metric)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(422, "invalid cursor")


def timestamp_fields(value, prefix="measured_at"):
    """Render the naive-UTC database convention explicitly in UTC and site-local time."""
    if value is None:
        return {f"{prefix}_utc": None, f"{prefix}_local": None}
    aware_utc = value.replace(tzinfo=timezone.utc)
    return {
        f"{prefix}_utc": aware_utc.isoformat().replace("+00:00", "Z"),
        f"{prefix}_local": aware_utc.astimezone(LOCAL_TIMEZONE).isoformat(),
    }


def freshness(measured_at, interval_seconds, quality="good"):
    stale_after = max(120, interval_seconds * 2)
    age = max(0.0, (utc_now() - measured_at).total_seconds())
    if quality == "error":
        state = "ERROR"
    else:
        state = "LIVE" if age <= stale_after else "STALE"
    return {
        "age_seconds": round(age, 3),
        "stale_after_seconds": stale_after,
        "data_status": state,
    }


@app.get("/api/v1/health")
def health():
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT count(*) FILTER (WHERE delivery_state IN ('PENDING','IN_FLIGHT')),
                    min(created_at) FILTER (WHERE delivery_state IN ('PENDING','IN_FLIGHT'))
                    FROM alarm_outbox""")
                pending, oldest = cur.fetchone()
        age = max(0.0, (utc_now() - oldest).total_seconds()) if oldest else 0.0
        state = "DEGRADED" if age > 300 else "UP"
        return {"service": "nextwireless-data-api", "status": state,
                "database": "UP", "outbox": {"pending": pending,
                "oldest_pending_age_seconds": round(age, 3),
                "alert": age > 300}}
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


@app.get("/api/v1/ingestion/metrics")
def ingestion_metrics(org_code: str = Depends(tenant_org)):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT metric_name,error_code,metric_value,latency_sum_ms,updated_at
                FROM ingestion_metrics WHERE org_code=%s ORDER BY metric_name,error_code""", (org_code,))
            rows = cur.fetchall()
    return {"organization": org_code, "counters": [{**row,
        "average_latency_ms": round(row["latency_sum_ms"] / row["metric_value"], 3)
            if row["metric_value"] else 0.0,
        "updated_at": row["updated_at"].isoformat() + "Z"} for row in rows]}


@app.get("/api/v1/devices/{device_id}/latest")
def latest(device_id: str, org_code: str = Depends(tenant_org)):
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT d.device_id,d.org_code,d.profile_code,d.telemetry_interval_seconds,
                    d.heartbeat_interval_seconds,s.current_state
                FROM devices d LEFT JOIN device_status s ON s.device_id=d.device_id
                WHERE d.device_id=%s AND d.org_code=%s AND d.enabled=TRUE""", (device_id, org_code))
            device = cur.fetchone()
            if device is None:
                raise HTTPException(status_code=404, detail="Device not found")
            cur.execute("""SELECT boot_id, seq, quality, measured_at, received_at
                FROM telemetry_reports WHERE device_id=%s
                ORDER BY measured_at DESC, id DESC LIMIT 1""", (device_id,))
            report = cur.fetchone()
            rows = []
            if report:
                cur.execute("""SELECT t.metric, t.value_number, p.unit, t.ts_source
                    FROM telemetry t
                    LEFT JOIN device_profile_points p
                      ON p.profile_code=%s AND p.channel='telemetry' AND p.point_code=t.metric
                    WHERE t.device_id=%s AND t.boot_id=%s AND t.seq=%s
                    ORDER BY t.metric""", (device["profile_code"], device_id,
                                            report["boot_id"], report["seq"]))
                rows = cur.fetchall()
            cur.execute("""SELECT event_id,event_type,event_at,payload_json
                FROM device_events WHERE device_id=%s ORDER BY event_at DESC LIMIT 1""", (device_id,))
            event = cur.fetchone()
            cur.execute("""SELECT boot_id,seq,measured_at,received_at,ts_source,state_json,payload_json
                FROM device_state_snapshots WHERE device_id=%s
                ORDER BY measured_at DESC,seq DESC LIMIT 1""", (device_id,))
            snapshot = cur.fetchone()
    latest_event = None
    if event:
        data = (event["payload_json"] or {}).get("data", {})
        latest_event = {"event_id": event["event_id"], "event_type": event["event_type"],
                        "event_at": event["event_at"].isoformat() + "Z",
                        "door_state": data.get("door_state")}
    base = {"device_id": device_id, "organization": device["org_code"],
            "profile_code": device["profile_code"],
            "online_status": device["current_state"] or "offline",
            "timezone": LOCAL_TIMEZONE_NAME, "schema_version": 1}
    latest_snapshot = None
    if snapshot:
        snapshot_times = timestamp_fields(snapshot["measured_at"])
        latest_snapshot = {"boot_id": snapshot["boot_id"], "seq": snapshot["seq"],
            "door_state": (snapshot["state_json"] or {}).get("door_state"),
            "ts_source": snapshot["ts_source"], **snapshot_times,
            **timestamp_fields(snapshot["received_at"], "received_at"),
            **freshness(snapshot["measured_at"], device["heartbeat_interval_seconds"])}
    if report is None:
        return {**base, "boot_id": snapshot["boot_id"] if snapshot else None,
                "seq": snapshot["seq"] if snapshot else None, "quality": None,
                "data_status": latest_snapshot["data_status"] if latest_snapshot else "NO_DATA",
                "metrics": [], "latest_event": latest_event,
                "latest_snapshot": latest_snapshot,
                "door_state": latest_snapshot["door_state"] if latest_snapshot else
                              (latest_event or {}).get("door_state")}
    times = timestamp_fields(report["measured_at"])
    received_times = timestamp_fields(report["received_at"], "received_at")
    fresh = freshness(report["measured_at"], device["telemetry_interval_seconds"], report["quality"])
    return {**base, "boot_id": report["boot_id"], "seq": report["seq"],
            "quality": report["quality"], "timezone": LOCAL_TIMEZONE_NAME, **times,
            **received_times, **fresh, "metrics": [
        {"metric": r["metric"], "value": r["value_number"], "unit": r["unit"],
         "quality": report["quality"], "ts_source": r["ts_source"],
         "measured_at": times["measured_at_utc"], **times} for r in rows],
          "latest_event": latest_event, "latest_snapshot": latest_snapshot,
          "door_state": latest_snapshot["door_state"] if latest_snapshot else
                        (latest_event or {}).get("door_state")}


@app.get("/api/v1/devices/{device_id}/telemetry")
def telemetry(device_id: str, metric: str | None = None,
              from_: datetime | None = Query(default=None, alias="from"),
              to: datetime | None = Query(default=None),
              agg: str | None = Query(default=None, pattern="^(avg|min|max)$"),
              step: str | None = Query(default=None),
              limit: int = Query(default=100, ge=1, le=1000),
              cursor: str | None = Query(default=None),
              org_code: str = Depends(tenant_org)):
    start, end, after = utc_parameter(from_, "from"), utc_parameter(to, "to"), decode_cursor(cursor)
    if start and end and start >= end:
        raise HTTPException(422, "from must be before to")
    if bool(agg) != bool(step):
        raise HTTPException(422, "agg and step must be supplied together")
    if step and not re.fullmatch(r"[1-9][0-9]*\s*(second|minute|hour|day)s?", step):
        raise HTTPException(422, "step must be a positive second/minute/hour/day interval")
    if agg and cursor:
        raise HTTPException(422, "cursor is not supported with aggregation")
    with db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT device_id, profile_code, telemetry_interval_seconds
                FROM devices WHERE device_id=%s AND org_code=%s AND enabled=TRUE""", (device_id, org_code))
            device = cur.fetchone()
            if device is None:
                raise HTTPException(status_code=404, detail="Device not found")
            filters = """t.device_id=%s AND (%s::text IS NULL OR t.metric=%s)
                AND (%s::timestamp IS NULL OR t.measured_at >= %s)
                AND (%s::timestamp IS NULL OR t.measured_at < %s)"""
            params = [device_id, metric, metric, start, start, end, end]
            cur.execute(f"SELECT count(*) FROM telemetry t WHERE {filters}", params)
            total = cur.fetchone()["count"]
            if agg:
                expression = {"avg":"avg", "min":"min", "max":"max"}[agg]
                cur.execute(f"""SELECT t.metric, {expression}(t.value_number) AS value_number,
                    NULL::text AS boot_id,NULL::bigint AS seq,time_bucket(%s::interval,t.measured_at) AS measured_at,
                    max(t.received_at) AS received_at,'server'::text AS ts_source,'good'::text AS quality,p.unit
                    FROM telemetry t LEFT JOIN devices d ON d.device_id=t.device_id
                    LEFT JOIN device_profile_points p ON p.profile_code=d.profile_code
                      AND p.channel='telemetry' AND p.point_code=t.metric
                    WHERE {filters} GROUP BY t.metric,p.unit,time_bucket(%s::interval,t.measured_at)
                    ORDER BY measured_at DESC,t.metric LIMIT %s""", [step, *params, step, limit])
            else:
                cursor_filter = ""
                query_params = [device["profile_code"], *params]
                if after:
                    cursor_filter = " AND (t.measured_at,t.metric) < (%s,%s)"
                    query_params.extend(after)
                query_params.append(limit + 1)
                cur.execute(f"""SELECT t.metric, t.value_number, t.boot_id, t.seq, t.measured_at,
                    t.received_at, t.ts_source, r.quality, p.unit
                FROM telemetry t
                LEFT JOIN telemetry_reports r
                  ON (r.device_id,r.boot_id,r.seq)=(t.device_id,t.boot_id,t.seq)
                LEFT JOIN device_profile_points p
                  ON p.profile_code=%s AND p.channel='telemetry' AND p.point_code=t.metric
                WHERE {filters}{cursor_filter}
                ORDER BY t.measured_at DESC, t.metric DESC LIMIT %s""", query_params)
            rows = cur.fetchall()
    has_more = not agg and len(rows) > limit
    rows = rows[:limit]
    points = []
    for row in rows:
        quality = row["quality"] or "good"
        times = timestamp_fields(row["measured_at"])
        points.append({"metric": row["metric"], "value": row["value_number"],
            "unit": row["unit"], "quality": quality, "boot_id": row["boot_id"],
            "seq": row["seq"], "ts_source": row["ts_source"],
            "measured_at": times["measured_at_utc"], **times,
            **timestamp_fields(row["received_at"], "received_at"),
            **freshness(row["measured_at"], device["telemetry_interval_seconds"], quality)})
    next_cursor = encode_cursor(rows[-1]["measured_at"], rows[-1]["metric"]) if has_more else None
    return {"device_id": device_id, "timezone": LOCAL_TIMEZONE_NAME, "total": total,
            "limit": limit, "next_cursor": next_cursor, "aggregation": agg,
            "step": step, "points": points}


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
